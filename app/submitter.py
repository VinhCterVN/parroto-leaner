import asyncio
import re
from typing import List, Optional, Any
import httpx

from .models.card import Card, SubmitResult
from .config import config

SUBMIT_URL = "https://api.parroto.app/api/learning-vocabulary/submit"


class CardSubmitter:
    def __init__(
        self,
        bearer_token: Optional[str] = None,
        rating: Optional[str] = None,
        concurrency: Optional[int] = None,
        token_manager: Optional[Any] = None,
    ):
        self.token_manager = token_manager
        self.token = bearer_token or config.bearer_token
        self.rating = rating or config.rating
        self.concurrency = concurrency or config.concurrency

    async def _send_request(
        self,
        client: httpx.AsyncClient,
        card: Card,
        index: int,
        total: int,
        refreshed_retry: bool = False,
    ) -> SubmitResult:
        token = await self.token_manager.get_token() if self.token_manager else self.token
        payload = {
            "cardId": card.card_id,
            "rating": self.rating,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

        try:
            res = await client.post(SUBMIT_URL, json=payload, headers=headers, timeout=20.0)
            try:
                data = res.json()
            except Exception:
                data = {}

            status = data.get("status")
            message = data.get("message", res.text[:120]).strip()
            card_label = card.word if card.word else f"Card {card.card_id[:8]}.."

            if res.status_code == 200 and status == "success":
                card_data = data.get("data", {}) or {}
                diamonds = card_data.get("diamonds_earned", 0)
                streak = card_data.get("new_streak")
                next_review = card_data.get("next_review_at")
                print(
                    f"[{index}/{total}] [SUCCESS] {card_label:<20} "
                    f"+{diamonds} diamond(s) | Streak: {streak}"
                )
                return SubmitResult(
                    card_id=card.card_id,
                    word=card.word or card.card_id,
                    status="success",
                    status_code=res.status_code,
                    message=message,
                    diamonds_earned=diamonds,
                    new_streak=streak,
                    next_review_at=next_review,
                )

            # Unauthorized (401) - Try auto-refreshing token
            if res.status_code == 401:
                if not refreshed_retry and self.token_manager and self.token_manager.can_refresh:
                    print(f"[{index}/{total}] [AUTH 401] Token expired! Auto-refreshing access token...")
                    try:
                        await self.token_manager.refresh()
                        # Retry request once with the new token
                        return await self._send_request(client, card, index, total, refreshed_retry=True)
                    except Exception as refresh_err:
                        print(f"[{index}/{total}] [AUTH_ERROR] Token refresh failed: {refresh_err}")

                print(f"[{index}/{total}] [AUTH_ERROR] Token expired or invalid! (HTTP 401)")
                return SubmitResult(
                    card_id=card.card_id,
                    word=card.word or card.card_id,
                    status="error",
                    status_code=res.status_code,
                    message="Token expired or unauthorized (401)",
                )

            # Delayed / cooldown response
            if "Thời gian còn lại" in message or "chưa thể review" in message:
                rem_match = re.search(r"Thời gian còn lại:\s*([^\"]+)", message)
                remaining = rem_match.group(1).strip() if rem_match else message
                print(
                    f"[{index}/{total}] [DELAYED] {card_label:<20} (ID: {card.card_id}) "
                    f"Cooldown: {remaining}"
                )
                return SubmitResult(
                    card_id=card.card_id,
                    word=card.word or card.card_id,
                    status="delayed",
                    status_code=res.status_code,
                    message=message,
                    remaining_time=remaining,
                )

            # 429 Too Many Requests
            if res.status_code == 429 or "Too many requests" in message:
                print(f"[{index}/{total}] [ERROR 429] {card_label} (ID: {card.card_id}) -> {message}")
                return SubmitResult(
                    card_id=card.card_id,
                    word=card.word or card.card_id,
                    status="error",
                    status_code=429,
                    message=message,
                )

            # Other errors
            print(f"[{index}/{total}] [ERROR {res.status_code}] {card_label} -> {message}")
            return SubmitResult(
                card_id=card.card_id,
                word=card.word or card.card_id,
                status="error",
                status_code=res.status_code,
                message=message,
            )

        except httpx.TimeoutException:
            print(f"[{index}/{total}] [TIMEOUT] {card.card_id} request timed out")
            return SubmitResult(
                card_id=card.card_id,
                word=card.word or card.card_id,
                status="error",
                status_code=408,
                message="Request timeout",
            )
        except Exception as e:
            print(f"[{index}/{total}] [FAIL] {card.card_id}: {e}")
            return SubmitResult(
                card_id=card.card_id,
                word=card.word or card.card_id,
                status="error",
                status_code=0,
                message=str(e),
            )

    async def submit_single(
        self,
        client: httpx.AsyncClient,
        card: Card,
        semaphore: asyncio.Semaphore,
        index: int,
        total: int,
    ) -> SubmitResult:
        async with semaphore:
            return await self._send_request(client, card, index, total)

    async def submit_all(self, cards: List[Card]) -> List[SubmitResult]:
        """Submit all cards concurrently using asyncio and semaphore."""
        if not cards:
            print("[-] No cards provided to submit.")
            return []

        semaphore = asyncio.Semaphore(self.concurrency)
        total = len(cards)
        print(f"[*] Submitting {total} cards SIMULTANEOUSLY (concurrency: {self.concurrency}, rating: '{self.rating}')...\n")

        async with httpx.AsyncClient(timeout=25.0) as client:
            tasks = [
                self.submit_single(client, card, semaphore, i, total)
                for i, card in enumerate(cards, 1)
            ]
            results = await asyncio.gather(*tasks)

        self._print_summary(results, total, mode_name="SIMULTANEOUS BATCH")
        return results

    async def submit_linear(
        self,
        cards: List[Card],
        delay: float = 0.5,
        rate_limit_delay: float = 10.0,
        max_429_retries: int = 3,
    ) -> List[SubmitResult]:
        """
        Submit cards linearly (one by one in sequence).
        If HTTP 429 Too Many Requests is encountered, delays for rate_limit_delay seconds and retries.
        """
        if not cards:
            print("[-] No cards provided to submit.")
            return []

        total = len(cards)
        print(f"[*] Submitting {total} cards LINEARLY (delay: {delay}s, 429 cooldown: {rate_limit_delay}s, rating: '{self.rating}')...\n")

        results: List[SubmitResult] = []
        async with httpx.AsyncClient(timeout=25.0) as client:
            for i, card in enumerate(cards, 1):
                attempt = 0
                while attempt <= max_429_retries:
                    res = await self._send_request(client, card, i, total)

                    if res.status_code == 429 or "Too many requests" in res.message:
                        attempt += 1
                        if attempt <= max_429_retries:
                            print(
                                f"         [COOLDOWN] Too many requests! Pausing for {rate_limit_delay}s "
                                f"before retry ({attempt}/{max_429_retries})..."
                            )
                            await asyncio.sleep(rate_limit_delay)
                            continue
                        else:
                            print(f"         [COOLDOWN] Max 429 retries ({max_429_retries}) reached for card {card.card_id}.")
                            results.append(res)
                            break
                    else:
                        results.append(res)
                        break

                # Sleep standard delay between cards
                if delay > 0 and i < total:
                    await asyncio.sleep(delay)

        self._print_summary(results, total, mode_name="LINEAR BATCH")
        return results

    @staticmethod
    def _print_summary(results: List[SubmitResult], total: int, mode_name: str = "BATCH"):
        successes = sum(1 for r in results if r.status == "success")
        delayed = sum(1 for r in results if r.status == "delayed")
        rate_limited = sum(1 for r in results if r.status_code == 429)
        errors = sum(1 for r in results if r.status == "error")
        total_diamonds = sum(r.diamonds_earned for r in results)

        print("\n" + "=" * 55)
        print(f"                 {mode_name.upper()} SUMMARY")
        print("=" * 55)
        print(f"  Total Cards Processed: {total}")
        print(f"  [+] Successful Submits: {successes} (+{total_diamonds} diamonds)")
        print(f"  [-] Delayed (Cooldown):  {delayed}")
        if rate_limited > 0:
            print(f"  [!] Rate-limited (429):  {rate_limited}")
        print(f"  [x] Errors / Failed:     {errors}")
        print("=" * 55 + "\n")
