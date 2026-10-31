"""Rule-based эмоциональная разметка и настроение сцен (фоллбэк без LLM)."""
from __future__ import annotations

import re
from collections import Counter

from app.text.structure import Segment

LEXICON = {
    "anger": ("окаянн", "мерзав", "болван", "чёрт", "черт", "сволоч", "негодя", "шельм", "подл", "врёшь", "вреш",
              "истреб", "злоб", "ярост", "гнев", "проклят", "дурак", "дурацк", "не рассуждать", "убью", "пошёл вон"),
    "fear": ("страх", "страшн", "боюсь", "ужас", "дрож", "трепет", "испуг", "жутк", "спасите", "помогите", "беда"),
    "joy": ("рад", "весел", "смеш", "хохоч", "ха-ха", "смеёт", "смеет", "улыб", "счаст", "ура", "чудесн", "прелест"),
    "sadness": ("жаль", "увы", "плак", "слёз", "слез", "тоск", "печал", "горе", "уныл", "умер", "смерть", "одинок", "вздохн"),
    "surprise": ("неужели", "вот как", "ишь ты", "батюшки", "господи", "как же так", "вдруг", "не знал"),
    "tenderness": ("милый", "милая", "голубчик", "родн", "любл", "любим", "нежн", "дорог", "ласк"),
}
REMARK_EMOTION = {
    "anger": ("крич", "крикн", "закрич", "рявкн", "грози", "пригрози", "огрызн", "строго", "сердит", "злобно"),
    "fear": ("дрожа", "испуганн", "пролепета"),
    "joy": ("засмея", "хохоч", "улыбн", "радостн", "весело", "умилени"),
    "sadness": ("вздохн", "печальн", "грустн", "всхлипн", "плач"),
    "whisper": ("шепн", "шепч", "прошепт", "тихо", "вполголоса"),
    "surprise": ("удивлённ", "удивленн", "изумлённ", "воскликн"),
}
MOOD_OF = {
    "anger": "tension", "fear": "tension", "excitement": "action", "joy": "light", "irony": "light",
    "surprise": "light", "sadness": "sadness", "tenderness": "romance", "neutral": "calm", "whisper": "tension",
}
ACTION_WORDS = ("бежит", "бежал", "гонит", "прыга", "падает", "упал", "хвата", "удар", "выстрел", "мчал", "мчит",
                "кинул", "бросил", "схватил", "драк", "погон", "рвану", "толпа", "визг", "крик")
ROMANCE_WORDS = ("поцел", "любов", "обнял", "объят", "сердце", "нежн", "влюб", "свидани")


def tag_segment(seg: Segment) -> None:
    low = seg.text.lower()
    hint = (seg.attribution_hint or "").lower()
    scores: Counter = Counter()
    for emo, words in LEXICON.items():
        for w in words:
            if w in low:
                scores[emo] += 1
    for emo, words in REMARK_EMOTION.items():
        for w in words:
            if w in hint:
                scores[emo] += 3
    excl = low.count("!")
    if seg.kind == "dialogue" and excl >= 2:
        scores["excitement"] += 1
        scores["anger"] += 0.5 if scores["anger"] else 0
    if low.count("…") >= 2 and not scores:
        scores["sadness"] += 0.5
    if seg.kind == "narration":
        for w in ACTION_WORDS:
            if w in low:
                scores["excitement"] += 0.7
    if not scores or max(scores.values()) < 0.6:
        seg.emotion = "neutral"
        seg.intensity = 0.2 if seg.kind == "narration" else 0.35
    else:
        emo, val = scores.most_common(1)[0]
        seg.emotion = emo
        seg.intensity = round(min(1.0, 0.35 + 0.15 * val + 0.08 * excl), 2)
    if seg.kind == "narration":
        seg.intensity = min(seg.intensity, 0.5)


def scene_moods(segs: list[Segment], min_paragraphs: int = 4) -> list[dict]:
    """Делит главу на сцены по абзацам и подбирает настроение.

    Возвращает [{"start": индекс сегмента, "mood": ...}]. Соседние одинаковые сцены сливаются,
    короткие (< min_paragraphs абзацев) присоединяются к предыдущей — чтобы музыка не «дёргалась».
    """
    if not segs:
        return []
    # окна по ~6 абзацев
    windows: list[tuple[int, list[Segment]]] = []
    cur, start, pars = [], 0, set()
    for i, s in enumerate(segs):
        if not cur:
            start = i
        cur.append(s)
        pars.add(s.paragraph)
        if len(pars) >= 6 or i == len(segs) - 1:
            windows.append((start, cur))
            cur, pars = [], set()
    scenes = []
    for start, win in windows:
        c: Counter = Counter()
        text = " ".join(s.text.lower() for s in win)
        for s in win:
            c[MOOD_OF.get(s.emotion, "calm")] += s.intensity * (1.5 if s.kind == "dialogue" else 1)
        c["action"] += 0.4 * sum(text.count(w) for w in ACTION_WORDS)
        c["romance"] += 0.6 * sum(text.count(w) for w in ROMANCE_WORDS)
        c["calm"] += 0.8
        mood = c.most_common(1)[0][0]
        scenes.append({"start": start, "mood": mood, "paragraphs": len({s.paragraph for s in win})})
    merged: list[dict] = []
    for sc in scenes:
        if merged and (merged[-1]["mood"] == sc["mood"] or sc["paragraphs"] < min_paragraphs // 2):
            merged[-1]["paragraphs"] += sc["paragraphs"]
            continue
        merged.append(dict(sc))
    for m in merged:
        m.pop("paragraphs", None)
    return merged
