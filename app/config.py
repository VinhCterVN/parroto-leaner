import math
import os
import random
from pathlib import Path
from dotenv import load_dotenv

DelaySpec = float | tuple[float, float]

# Load environment variables from .env if present
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def clean_token(token: str) -> str:
    if not token:
        return ""
    token = token.strip()
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def parse_delay(value: DelaySpec | list[float] | str) -> DelaySpec:
    """Parse a fixed delay (seconds) or a [min, max] random delay range."""
    if isinstance(value, bool):
        raise ValueError("--delay must be a finite, non-negative number or [min, max] range")
    if isinstance(value, (int, float)):
        num = float(value)
        if not math.isfinite(num) or num < 0:
            raise ValueError("--delay must be a finite, non-negative number")
        return num
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError("--delay range must have exactly two numbers: [min, max]")
        low_raw, high_raw = value
        if isinstance(low_raw, bool) or isinstance(high_raw, bool):
            raise ValueError("--delay range values must be finite, non-negative numbers")
        low = float(low_raw)
        high = float(high_raw)
        if not math.isfinite(low) or not math.isfinite(high) or low < 0 or high < 0:
            raise ValueError("--delay range values must be finite, non-negative numbers")
        if low > high:
            raise ValueError("--delay range minimum must not exceed maximum")
        return (low, high)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.endswith("]"):
            if not (text.startswith("[") and text.endswith("]")):
                raise ValueError("--delay range must be formatted as [min, max]")
            inner = text[1:-1].strip()
            parts = [part.strip() for part in inner.split(",")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError("--delay range must be formatted as [min, max]")
            try:
                low = float(parts[0])
                high = float(parts[1])
            except ValueError as exc:
                raise ValueError("--delay range must contain valid numbers: [min, max]") from exc
            return parse_delay((low, high))
        try:
            num = float(text)
        except ValueError as exc:
            raise ValueError("--delay must be a finite, non-negative number or [min, max] range") from exc
        return parse_delay(num)
    raise ValueError("--delay must be a finite, non-negative number or [min, max] range")


def sample_delay(delay: DelaySpec) -> float:
    """Return a fixed delay or a 2-decimal random delay within [min, max]."""
    if isinstance(delay, tuple):
        low, high = delay
        return round(random.uniform(low, high), 2)
    return delay


def format_delay(delay: DelaySpec) -> str:
    """Format a fixed delay or [min, max] range for log output."""
    if isinstance(delay, tuple):
        return f"[{delay[0]}, {delay[1]}]s"
    return f"{delay}s"


class Config:
    def __init__(self):
        raw_token = os.getenv("BEARER_TOKEN") or os.getenv("ACCESS_TOKEN") or ""
        self.bearer_token: str = clean_token(raw_token)
        self.refresh_token: str = clean_token(os.getenv("REFRESH_TOKEN") or os.getenv("FIREBASE_REFRESH_TOKEN") or "")
        self.api_key: str = clean_token(os.getenv("API_KEY") or os.getenv("FIREBASE_API_KEY") or "")

        self.build_id: str = os.getenv("BUILD_ID", "bWYuKDgCvminqE7L8hyo1").strip().strip('"').strip("'")
        self.concurrency: int = int(os.getenv("CONCURRENCY", "10"))
        self.rating: str = os.getenv("RATING", "again").strip().strip('"').strip("'")
        self.cards_file: Path = ROOT_DIR / os.getenv("CARDS_FILE", "data/cards.json")
        self.deck_id: str = (os.getenv("DECK_ID") or os.getenv("deckId") or "").strip().strip('"').strip("'")
        self.loop_interval_minutes: float = float(os.getenv("LOOP_INTERVAL_MINUTES", "10"))
        self.linear_delay: DelaySpec = parse_delay(os.getenv("LINEAR_DELAY", "0.5"))
        self.cooldown_429: float = float(os.getenv("COOLDOWN_429", "10.0"))
        self.heartbeat_interval_minutes: float = float(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "5"))
        self.heartbeat_enabled: bool = os.getenv("ENABLE_HEARTBEAT", "false").lower() in ("true", "1", "yes")

    def has_auth(self) -> bool:
        has_bearer = bool(self.bearer_token and self.bearer_token not in ("abc_zyz", "your_bearer_token_here"))
        has_refresh = bool(self.refresh_token and self.api_key)
        return has_bearer or has_refresh

    def validate(self) -> None:
        if not self.has_auth():
            raise ValueError(
                "No valid authentication credentials found!\n"
                "Please configure either:\n"
                "1. REFRESH_TOKEN and API_KEY in your .env (Recommended for auto-refresh),\n"
                "   OR\n"
                "2. BEARER_TOKEN in .env (or via --token)."
            )


config = Config()
