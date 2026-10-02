import os
from pathlib import Path
from dotenv import load_dotenv

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
        self.loop_interval_minutes: float = float(os.getenv("LOOP_INTERVAL_MINUTES", "10"))
        self.linear_delay: float = float(os.getenv("LINEAR_DELAY", "0.5"))
        self.cooldown_429: float = float(os.getenv("COOLDOWN_429", "10.0"))

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
