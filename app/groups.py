"""Load deck/group card data and run reset-before-review rounds."""

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from .auth import TokenManager
from .models.card import Card
from .submitter import CardSubmitter

API_BASE = "https://api.parroto.app/api/learning-vocabulary"


class GroupLoopError(RuntimeError):
    """A round cannot safely continue with the available data or API response."""


@dataclass
class CardGroup:
    group_id: str
    cards: list[Card]


@dataclass
class GroupDeck:
    deck_id: str
    groups: list[CardGroup]


def _identifier(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    if value in (".", "..") or any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid {label}")
    return value


def load_group_decks(source: Path, deck_id: str | None = None) -> list[GroupDeck]:
    """Read a JSON file or directory; examples are excluded from directory scans."""
    source = Path(source)
    selected = _identifier(deck_id, "deckId selection") if deck_id is not None else None
    files = sorted(p for p in source.glob("*.json") if not p.name.endswith(".example.json")) if source.is_dir() else [source]
    if not files or any(not path.is_file() for path in files):
        raise ValueError(f"No valid deck data found at {source}. Add a populated JSON file first.")

    decks = []
    seen_decks = set()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read deck data from {path}: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected an array of decks")
        for row in data:
            if not isinstance(row, dict):
                raise ValueError(f"{path}: each deck must be an object")
            current_id = _identifier(row.get("deckId"), f"{path}: deckId")
            if selected is not None and current_id != selected:
                continue
            if current_id in seen_decks:
                raise ValueError(f"{path}: duplicate deckId {current_id}; combine its groups in one entry")
            rows = row.get("groups")
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"{path}: deck {current_id} must contain non-empty groups")
            groups = []
            seen_groups, seen_cards = set(), set()
            for group in rows:
                if not isinstance(group, dict):
                    raise ValueError(f"{path}: groups must be objects")
                group_id = _identifier(group.get("groupId"), f"{path}: groupId")
                if group_id in seen_groups:
                    raise ValueError(f"{path}: duplicate groupId {group_id} in deck {current_id}")
                card_ids = group.get("cards")
                if not isinstance(card_ids, list) or not card_ids:
                    raise ValueError(f"{path}: group {group_id} must contain non-empty cards")
                cards = []
                for raw_id in card_ids:
                    card_id = _identifier(raw_id, f"{path}: card ID")
                    if card_id in seen_cards:
                        raise ValueError(f"{path}: duplicate card ID {card_id} in deck {current_id}")
                    seen_cards.add(card_id)
                    cards.append(Card(card_id=card_id, word=""))
                seen_groups.add(group_id)
                groups.append(CardGroup(group_id=group_id, cards=cards))
            seen_decks.add(current_id)
            decks.append(GroupDeck(deck_id=current_id, groups=groups))
    if not decks:
        selection = f" for deckId {selected}" if selected is not None else ""
        raise ValueError(f"No valid deck data found{selection} at {source}")
    return decks


class GroupProgressClient:
    def __init__(self, token_manager: TokenManager, cooldown_429: float = 10.0):
        self.token_manager = token_manager
        self.cooldown_429 = cooldown_429

    async def _request(self, client: httpx.AsyncClient, method: str, path: str):
        refreshed = False
        retries_429 = 0
        while True:
            try:
                token = await self.token_manager.get_token()
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                raise GroupLoopError(f"{method} {path}: could not obtain a Bearer token") from exc
            if not token:
                raise GroupLoopError("No Bearer token available for group progress requests")
            try:
                response = await client.request(
                    method, f"{API_BASE}/{path}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=20.0,
                )
            except httpx.RequestError as exc:
                raise GroupLoopError(f"{method} {path}: network request failed ({type(exc).__name__})") from exc
            if response.status_code == 401 and not refreshed and self.token_manager.can_refresh:
                refreshed = True
                try:
                    await self.token_manager.refresh()
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    raise GroupLoopError(f"{method} {path}: Bearer token refresh failed") from exc
                continue
            if response.status_code == 429 and retries_429 < 3:
                retries_429 += 1
                print(f"[GROUP] HTTP 429; waiting {self.cooldown_429}s before retry {retries_429}/3")
                await asyncio.sleep(self.cooldown_429)
                continue
            if not 200 <= response.status_code < 300:
                raise GroupLoopError(f"{method} {path}: HTTP {response.status_code}; stopping group loop")
            if not response.content:
                if method == "DELETE":
                    return None
                raise GroupLoopError(f"{method} {path}: empty progress response")
            try:
                payload = response.json()
            except ValueError as exc:
                raise GroupLoopError(f"{method} {path}: expected a JSON response") from exc
            if method == "DELETE" and not isinstance(payload, dict):
                raise GroupLoopError(f"{method} {path}: unrecognized reset response")
            if isinstance(payload, dict):
                if (payload.get("status") not in (None, "success")
                        or payload.get("success") is False or payload.get("error")):
                    raise GroupLoopError(f"{method} {path}: API reported failure")
            return payload

    async def get_group_ids(self, client: httpx.AsyncClient, deck_id: str) -> list[str]:
        """Parse explicit group lists; unknown schemas stop before any DELETE."""
        payload = await self._request(client, "GET", f"groups-progress/{quote(deck_id, safe='')}")
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows = data.get("groups") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not rows:
            raise GroupLoopError(f"Deck {deck_id}: no groups or unrecognized groups-progress response; expected a groups array")
        group_ids = []
        for row in rows:
            if not isinstance(row, dict):
                raise GroupLoopError(f"Deck {deck_id}: group entries must be objects")
            raw_id = row.get("groupId") or row.get("group_id") or row.get("id") or row.get("_id")
            try:
                group_id = _identifier(raw_id, "API group ID")
            except ValueError as exc:
                raise GroupLoopError(f"Deck {deck_id}: {exc}") from exc
            if group_id not in group_ids:
                group_ids.append(group_id)
        return group_ids

    async def reset_group(self, client: httpx.AsyncClient, deck_id: str, group_id: str) -> None:
        path = f"group/{quote(deck_id, safe='')}/{quote(group_id, safe='')}"
        await self._request(client, "DELETE", path)
        print(f"[GROUP] Reset deck {deck_id}, group {group_id}")


def validate_group_options(
    *,
    rating: str,
    concurrency: int,
    delay: float,
    cooldown_429: float,
    interval_minutes: float,
    rounds: int | None,
) -> None:
    """Reject invalid settings before authentication or progress requests."""
    if rounds is not None and (not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1):
        raise ValueError("--rounds must be a positive integer")
    if rating not in ("again", "hard", "good", "easy"):
        raise ValueError("RATING must be again, hard, good, or easy")
    if concurrency < 1:
        raise ValueError("--concurrency must be positive")
    for name, value in (("--delay", delay), ("--cooldown-429", cooldown_429), ("--interval", interval_minutes)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite, non-negative number")


async def run_group_loop(
    decks: list[GroupDeck],
    token_manager: TokenManager,
    submitter: CardSubmitter,
    *,
    linear: bool = False,
    delay: float = 0.5,
    cooldown_429: float = 10.0,
    interval_minutes: float = 0.0,
    rounds: int | None = None,
) -> None:
    """Reset all discovered groups before every round; stop on incomplete submits."""
    if not decks:
        raise ValueError("No valid decks available for group loop")
    validate_group_options(
        rating=submitter.rating, concurrency=submitter.concurrency,
        delay=delay, cooldown_429=cooldown_429,
        interval_minutes=interval_minutes, rounds=rounds,
    )
    progress = GroupProgressClient(token_manager, cooldown_429=cooldown_429)
    round_num = 1
    print(f"[*] Group loop: {len(decks)} deck(s), rating '{submitter.rating}'. Press Ctrl+C to stop.")
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            # Validate every selected deck's remote groups before resetting any deck.
            reset_plan = []
            for deck in decks:
                group_ids = await progress.get_group_ids(client, deck.deck_id)
                missing = {group.group_id for group in deck.groups} - set(group_ids)
                if missing:
                    raise GroupLoopError(f"Deck {deck.deck_id}: local groups absent from API: {', '.join(sorted(missing))}")
                reset_plan.append((deck, group_ids))

            print(f"\n--- GROUP LOOP ROUND {round_num}: RESETTING GROUPS ---")
            for deck, group_ids in reset_plan:
                for group_id in group_ids:
                    await progress.reset_group(client, deck.deck_id, group_id)

            # Do not start submissions until all resets have succeeded.
            for deck in decks:
                cards = [card for group in deck.groups for card in group.cards]
                print(f"[GROUP] Submitting {len(cards)} cards for deck {deck.deck_id}")
                try:
                    if linear:
                        results = await submitter.submit_linear(cards, delay=delay, rate_limit_delay=cooldown_429)
                    else:
                        results = await submitter.submit_all(cards)
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    raise GroupLoopError(f"Deck {deck.deck_id}: submission aborted ({type(exc).__name__})") from exc
                if (len(results) != len(cards)
                        or {result.card_id for result in results} != {card.card_id for card in cards}
                        or any(result.status != "success" for result in results)):
                    raise GroupLoopError(f"Deck {deck.deck_id}: incomplete or failed submission; stopped before another reset")

            print(f"[GROUP] Round {round_num} complete")
            if rounds is not None and round_num >= rounds:
                return
            if interval_minutes:
                print(f"[GROUP] Waiting {interval_minutes} minute(s) before resetting for the next round")
            await asyncio.sleep(interval_minutes * 60)
            round_num += 1
