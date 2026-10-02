import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
import httpx

from .models.card import Card
from .config import config


class CardScraper:
    def __init__(
        self,
        bearer_token: Optional[str] = None,
        build_id: Optional[str] = None,
        token_manager: Optional[Any] = None,
    ):
        self.token_manager = token_manager
        self.token = bearer_token or (token_manager.get_token_sync() if token_manager else config.bearer_token)
        self.build_id = build_id or config.build_id
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }

    def _get_cookies(self) -> Dict[str, str]:
        cookies = {}
        token = self.token_manager.get_token_sync() if self.token_manager else self.token
        if token:
            cookies["access_token"] = token
        return cookies

    def resolve_build_id(self, client: httpx.Client) -> str:
        """Verify configured build_id or extract the latest active buildId from parroto.app."""
        if self.build_id:
            test_url = f"https://parroto.app/_next/data/{self.build_id}/vi/vocabulary.json"
            try:
                res = client.get(test_url, timeout=10)
                if res.status_code == 200:
                    return self.build_id
            except Exception:
                pass

        # Fallback: scrape buildId from HTML of /vi/vocabulary
        try:
            res = client.get("https://parroto.app/vi/vocabulary", timeout=15)
            if res.status_code == 200:
                match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', res.text, re.DOTALL)
                if match:
                    next_data = json.loads(match.group(1))
                    found_id = next_data.get("buildId")
                    if found_id:
                        self.build_id = found_id
                        return found_id
        except Exception:
            pass

        return self.build_id

    def fetch_decks(self, client: httpx.Client, build_id: str) -> List[Dict[str, Any]]:
        """Fetch all available decks from the vocabulary directory."""
        url = f"https://parroto.app/_next/data/{build_id}/vi/vocabulary.json"
        res = client.get(url, cookies=self._get_cookies(), timeout=15)
        res.raise_for_status()
        data = res.json()
        decks = data.get("pageProps", {}).get("directoryDecks", [])
        return decks

    def fetch_deck_preview_cards(self, client: httpx.Client, build_id: str, slug: str) -> List[Dict[str, Any]]:
        """Fetch preview cards for a specific deck slug."""
        url = f"https://parroto.app/_next/data/{build_id}/vi/vocabulary/{slug}.json?slug={slug}"
        res = client.get(url, cookies=self._get_cookies(), timeout=15)
        if res.status_code != 200:
            return []
        data = res.json()
        props = data.get("pageProps", {})
        cards = props.get("previewCards") or []
        return cards

    def scrape_all(self, check_pro: bool = False) -> List[Card]:
        """
        Scrape preview cards from all accessible free decks (and pro decks if check_pro=True).
        Returns deduplicated list of Card objects.
        """
        with httpx.Client(headers=self.headers, timeout=20) as client:
            build_id = self.resolve_build_id(client)
            decks = self.fetch_decks(client, build_id)

            if not check_pro:
                target_decks = [d for d in decks if not d.get("is_pro")]
            else:
                target_decks = decks

            print(f"[*] Found {len(decks)} total decks ({len(target_decks)} accessible decks to scrape).")
            print(f"[*] Active build ID: {build_id}")

            all_cards: List[Card] = []
            seen_ids = set()

            for i, deck in enumerate(target_decks, 1):
                slug = deck.get("slug")
                deck_name = deck.get("name", slug)
                if not slug:
                    continue

                try:
                    preview_cards = self.fetch_deck_preview_cards(client, build_id, slug)
                    added_count = 0
                    for c in preview_cards:
                        cid = c.get("_id") or c.get("id")
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            card = Card(
                                card_id=cid,
                                word=c.get("word", ""),
                                deck_slug=slug,
                                deck_name=deck_name,
                                difficulty=c.get("difficulty"),
                                translation=c.get("translation"),
                            )
                            all_cards.append(card)
                            added_count += 1

                    print(f"  [{i}/{len(target_decks)}] {deck_name} ({slug}): {len(preview_cards)} preview cards found (+{added_count} unique)")
                except Exception as e:
                    print(f"  [{i}/{len(target_decks)}] Failed to fetch deck '{slug}': {e}")

            print(f"[+] Total unique cards collected: {len(all_cards)}")
            return all_cards

    @staticmethod
    def save_to_file(cards: List[Any], file_path: Path) -> None:
        """Save list of card IDs (array of strings) to JSON file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        card_ids = [
            c.card_id if hasattr(c, "card_id") else (c.get("card_id", "") if isinstance(c, dict) else str(c))
            for c in cards
        ]
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(card_ids, f, ensure_ascii=False, indent=2)
        print(f"[+] Successfully saved {len(card_ids)} card IDs to {file_path}")

    @staticmethod
    def load_from_file(file_path: Path) -> List[Card]:
        """Load cards from JSON file (supports list of ID strings or objects)."""
        if not file_path.exists():
            return []
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cards = []
        for item in data:
            if isinstance(item, str):
                cards.append(Card(card_id=item, word=""))
            elif isinstance(item, dict):
                cards.append(Card.from_dict(item))
        return cards
