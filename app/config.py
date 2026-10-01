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
        self.build_id: str = os.getenv("BUILD_ID", "bWYuKDgCvminqE7L8hyo1").strip().strip('"').strip("'")
        self.concurrency: int = int(os.getenv("CONCURRENCY", "10"))
        self.rating: str = os.getenv("RATING", "again").strip().strip('"').strip("'")
        self.cards_file: Path = ROOT_DIR / os.getenv("CARDS_FILE", "data/cards.json")
        self.loop_interval_minutes: int = int(os.getenv("LOOP_INTERVAL_MINUTES", "10"))
        self.linear_delay: float = float(os.getenv("LINEAR_DELAY", "0.5"))
        self.cooldown_429: float = float(os.getenv("COOLDOWN_429", "10.0"))

    def validate(self) -> None:
        if not self.bearer_token or self.bearer_token == "abc_zyz" or self.bearer_token == "your_bearer_token_here":
            raise ValueError(
                "BEARER_TOKEN is not set or is still the placeholder in .env!\n"
                "Please set your real Bearer token in .env or pass it via --token."
            )


config = Config()
