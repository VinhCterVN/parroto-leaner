import argparse
import asyncio
import sys
import time
from pathlib import Path

from .config import config, clean_token
from .auth import TokenManager
from .models.card import Card
from .scraper import CardScraper
from .submitter import CardSubmitter

# Ensure UTF-8 output for Vietnamese characters on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Parroto Bulk Cards Learner - Automatically scrape preview cards and submit bulk reviews."
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="run",
        choices=["fetch", "submit", "linear", "submit-linear", "run", "loop"],
        help="Action to perform: 'fetch' (scrape cards), 'submit' (simultaneous submit), 'linear' (one-by-one with 429 backoff), 'run' (fetch + submit), 'loop' (continuous). Default: 'run'",
    )
    parser.add_argument(
        "--linear",
        action="store_true",
        help="Execute submissions linearly (one by one) with automatic 429 rate limit backoff",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Delay in seconds between linear requests (default: 0.5s)",
    )
    parser.add_argument(
        "--cooldown-429",
        type=float,
        default=None,
        help="Cooldown in seconds to pause when HTTP 429 Too Many Requests is hit (default: 10.0s)",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Override Parroto Bearer/Access token (otherwise loaded from .env)",
    )
    parser.add_argument(
        "--refresh-token",
        type=str,
        default=None,
        help="Override Firebase/Google refresh token for auto-refresh (otherwise loaded from .env)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Override Firebase/Google API key (otherwise loaded from .env)",
    )
    parser.add_argument(
        "--build-id",
        type=str,
        default=None,
        help="Override Next.js build ID (default: read from .env or bWYuKDgCvminqE7L8hyo1)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Number of concurrent submit requests in simultaneous mode (default: 10)",
    )
    parser.add_argument(
        "--rating",
        type=str,
        default=None,
        choices=["again", "hard", "good", "easy"],
        help="Review rating level to send (default: 'again')",
    )
    parser.add_argument(
        "--cards-file",
        type=str,
        default=None,
        help="Path to JSON file for storing/loading cards (default: data/cards.json)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Interval in minutes between submit rounds in 'loop' mode (default: 10, set to 0 for no interval)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-scraping cards even if data/cards.json exists (used with 'run')",
    )
    parser.add_argument(
        "--check-pro",
        action="store_true",
        help="Attempt to scrape cards from pro decks as well (useful if your account is Pro)",
    )
    return parser.parse_args()


def do_fetch(scraper: CardScraper, cards_path: Path, check_pro: bool = False):
    print("\n--- [STEP 1] FETCHING CARDS FROM DECKS ---")
    cards = scraper.scrape_all(check_pro=check_pro)
    if cards:
        scraper.save_to_file(cards, cards_path)
    else:
        print("[!] No cards could be scraped. Please check your token or build ID.")
    return cards


async def do_submit(
    submitter: CardSubmitter,
    cards,
    linear: bool = False,
    delay: float = 0.5,
    cooldown_429: float = 10.0,
):
    print("\n--- [STEP 2] SUBMITTING REVIEWS ---")
    if linear:
        return await submitter.submit_linear(cards, delay=delay, rate_limit_delay=cooldown_429)
    else:
        return await submitter.submit_all(cards)


async def async_main():
    args = parse_args()

    # Initialize TokenManager
    token_manager = TokenManager(
        access_token=clean_token(args.token) if args.token else None,
        refresh_token=clean_token(args.refresh_token) if args.refresh_token else None,
        api_key=clean_token(args.api_key) if args.api_key else None,
    )

    # If no initial access token or token placeholder, attempt auto-refresh
    if not token_manager.access_token or token_manager.access_token in ("abc_zyz", "your_bearer_token_here"):
        if token_manager.can_refresh:
            print("[*] No active access token provided. Requesting fresh token using REFRESH_TOKEN...")
            try:
                await token_manager.refresh()
            except Exception as e:
                print(f"[ERROR] Failed to obtain token via REFRESH_TOKEN: {e}")
                sys.exit(1)
        else:
            print("[ERROR] No valid authentication credentials found!")
            print("Please configure either:")
            print("  1. REFRESH_TOKEN and API_KEY in your .env (Recommended - never expires!)")
            print("  2. Or set BEARER_TOKEN in .env (or pass --token <your_token>).")
            sys.exit(1)

    build_id = args.build_id or config.build_id
    concurrency = args.concurrency or config.concurrency
    rating = args.rating or config.rating
    cards_path = Path(args.cards_file) if args.cards_file else config.cards_file
    interval = args.interval if args.interval is not None else config.loop_interval_minutes
    linear_delay = args.delay if args.delay is not None else config.linear_delay
    cooldown_429 = args.cooldown_429 if args.cooldown_429 is not None else config.cooldown_429

    scraper = CardScraper(token_manager=token_manager, build_id=build_id)
    submitter = CardSubmitter(
        token_manager=token_manager,
        rating=rating,
        concurrency=concurrency,
    )

    action = args.action
    is_linear = args.linear or action in ("linear", "submit-linear")

    if action == "fetch":
        do_fetch(scraper, cards_path, check_pro=args.check_pro)

    elif action in ("submit", "linear", "submit-linear"):
        if args.refresh or not cards_path.exists():
            cards = do_fetch(scraper, cards_path, check_pro=args.check_pro)
        else:
            cards = scraper.load_from_file(cards_path)

        if not cards:
            print("[!] No cards available to submit.")
            sys.exit(1)
        await do_submit(submitter, cards, linear=is_linear, delay=linear_delay, cooldown_429=cooldown_429)

    elif action == "run":
        if args.refresh or not cards_path.exists():
            cards = do_fetch(scraper, cards_path, check_pro=args.check_pro)
        else:
            print(f"[*] Found existing card database at {cards_path}.")
            cards = scraper.load_from_file(cards_path)
            print(f"[*] Loaded {len(cards)} cards from disk. (Use --refresh to re-scrape from web).")

        if not cards:
            print("[!] No cards available to submit.")
            sys.exit(1)

        await do_submit(submitter, cards, linear=is_linear, delay=linear_delay, cooldown_429=cooldown_429)

    elif action == "loop":
        if args.refresh or not cards_path.exists():
            cards = do_fetch(scraper, cards_path, check_pro=args.check_pro)
        else:
            cards = scraper.load_from_file(cards_path)

        if not cards:
            print("[!] No cards available for loop mode.")
            sys.exit(1)

        mode_str = "LINEAR" if is_linear else f"SIMULTANEOUS (concurrency={concurrency})"
        print(f"\n[*] Starting Loop Mode ({mode_str}): will run every {interval} minute(s).")
        if token_manager.can_refresh:
            print("[*] Auto-refresh is ACTIVE: tokens will renew automatically in the background.")
        print("[*] Press Ctrl+C at any time to exit.")

        round_num = 1
        while True:
            print(f"\n{'#'*60}")
            print(f"### LOOP ROUND {round_num} - {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'#'*60}")

            # Ensure valid token before each loop iteration
            await token_manager.get_token()

            await do_submit(submitter, cards, linear=is_linear, delay=linear_delay, cooldown_429=cooldown_429)

            wait_secs = interval * 60
            if wait_secs > 0:
                print(f"[*] Round {round_num} completed. Sleeping for {interval} minute(s) ({wait_secs:.1f}s)...")
                try:
                    await asyncio.sleep(wait_secs)
                except asyncio.CancelledError:
                    break
            else:
                print(f"[*] Round {round_num} completed. Starting next round immediately without interval...")
                # Small yield to let any background tasks / cancellation be handled
                await asyncio.sleep(0)
            round_num += 1


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[!] Operation cancelled by user. Exiting cleanly.")


if __name__ == "__main__":
    main()
