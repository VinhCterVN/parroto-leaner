from dataclasses import dataclass, asdict
from typing import Optional, Any, Dict


@dataclass
class Card:
    card_id: str
    word: str
    deck_slug: str = ""
    deck_name: str = ""
    difficulty: Optional[str] = None
    translation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Card":
        return cls(
            card_id=data.get("card_id") or data.get("cardId") or data.get("_id", ""),
            word=data.get("word", ""),
            deck_slug=data.get("deck_slug") or data.get("deck", ""),
            deck_name=data.get("deck_name", ""),
            difficulty=data.get("difficulty"),
            translation=data.get("translation"),
        )


@dataclass
class SubmitResult:
    card_id: str
    word: str
    status: str  # "success", "delayed", "error"
    status_code: int
    message: str
    diamonds_earned: int = 0
    new_streak: Optional[int] = None
    next_review_at: Optional[str] = None
    remaining_time: Optional[str] = None
