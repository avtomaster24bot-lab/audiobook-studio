"""Определение структуры: главы → абзацы → сегменты (авторская речь / реплика)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Chapter

ROMAN = r"[IVXLCDM]{1,7}"
CHAPTER_RE = re.compile(
    rf"^\s*(?:(?:глава|часть|книга|chapter|part|раздел)\s+(?:{ROMAN}|\d+|[а-яё]+)\b.*"
    rf"|{ROMAN}\.?|\d{{1,3}}\.?|\*\s*\*\s*\*)\s*$",
    re.IGNORECASE,
)


def split_chapters(text: str, default_title: str = "Глава 1") -> list[Chapter]:
    """Делит нормализованный текст на главы по заголовкам вида «Глава I», «II», «* * *»."""
    lines = text.split("\n")
    chapters: list[Chapter] = []
    cur_title: str | None = None
    cur: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) < 80 and CHAPTER_RE.match(stripped):
            if cur and "".join(cur).strip():
                chapters.append(Chapter(cur_title or default_title, "\n".join(cur).strip()))
            cur_title = stripped if stripped not in ("* * *", "***") else f"Часть {len(chapters) + 2}"
            cur = []
        else:
            cur.append(line)
    if "".join(cur).strip():
        chapters.append(Chapter(cur_title or default_title, "\n".join(cur).strip()))
    if not chapters:
        chapters = [Chapter(default_title, text.strip())]
    # если глав слишком много мелких (ложные срабатывания на «1.» в тексте) — склеиваем
    if len(chapters) > 3 and sum(len(c.text) for c in chapters) / len(chapters) < 300:
        return [Chapter(default_title, text.strip())]
    return chapters


def paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    return paras


@dataclass
class Segment:
    """Единица озвучки: кусок авторского текста или реплика персонажа."""

    kind: str                 # "narration" | "dialogue"
    text: str
    paragraph: int
    speaker: str = "narrator"  # каноническое имя персонажа или narrator
    emotion: str = "neutral"
    intensity: float = 0.3     # 0..1
    attributed_by: str = ""    # rule:<name> / llm / turn-taking / unknown
    attribution_hint: str = "" # авторская ремарка рядом с репликой («— сказал Иван»)
    extra: dict = field(default_factory=dict)


DIALOGUE_START = re.compile(r"^\s*[—–-]\s*")


def split_dialogue_paragraph(par: str) -> list[tuple[str, str]]:
    """Разбивает абзац-диалог «— Реплика, — сказал Иван. — Ещё реплика.»
    на [("dialogue", "Реплика,"), ("narration", "сказал Иван."), ("dialogue", "Ещё реплика.")].
    """
    body = DIALOGUE_START.sub("", par, count=1)
    # разделитель между репликой и ремаркой: «, — », «! — », «? — », «… — », «. — »
    parts = re.split(r"(?<=[,!?…:.])\s*—\s+", body)
    out: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        kind = "dialogue" if i % 2 == 0 else "narration"
        out.append((kind, p))
    return out


QUOTE_RE = re.compile(r"«([^«»]{2,300})»")


def segment_chapter(text: str) -> list[Segment]:
    segs: list[Segment] = []
    for pi, par in enumerate(paragraphs(text)):
        if DIALOGUE_START.match(par):
            pieces = split_dialogue_paragraph(par)
            # ремарка, относящаяся к репликам абзаца
            hint = " ".join(t for k, t in pieces if k == "narration")
            for kind, t in pieces:
                segs.append(Segment(kind=kind, text=t, paragraph=pi, attribution_hint=hint if kind == "dialogue" else ""))
        else:
            segs.append(Segment(kind="narration", text=par, paragraph=pi))
    return segs
