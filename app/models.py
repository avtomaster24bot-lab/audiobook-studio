from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chapter:
    title: str
    text: str


@dataclass
class Book:
    title: str
    author: str = ""
    language: str = "ru"
    source: str = ""          # wikisource / gutenberg / librusec-like / url / file
    source_url: str = ""
    license_note: str = ""
    cover_url: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Book":
        d = dict(d)
        d["chapters"] = [Chapter(**c) for c in d.get("chapters", [])]
        return cls(**d)

    @property
    def full_text(self) -> str:
        return "\n\n".join(c.text for c in self.chapters)


class SourceError(Exception):
    """Ошибка модуля источников с понятным пользователю сообщением."""

    def __init__(self, message: str, code: str = "source_error", suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.suggestions = suggestions or []
