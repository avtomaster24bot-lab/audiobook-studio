"""Библиотека фоновой музыки и подбор трека под настроение сцены."""
from __future__ import annotations

from pathlib import Path

from app import config

MOODS = {
    "calm": "Покой",
    "tension": "Напряжение",
    "action": "Экшен",
    "sadness": "Печаль",
    "romance": "Романтика",
    "light": "Ирония / лёгкость",
}
# если трека для настроения нет — ближайшее по характеру
FALLBACK = {"light": "calm", "romance": "calm", "sadness": "calm", "action": "tension", "tension": "calm"}


def track_for(mood: str) -> Path | None:
    d = config.MUSIC_DIR
    m = mood
    for _ in range(3):
        for ext in (".mp3", ".ogg", ".wav", ".flac"):
            p = d / f"{m}{ext}"
            if p.exists():
                return p
        m = FALLBACK.get(m, "calm")
    return None


def library() -> list[dict]:
    return [{"mood": k, "title": v, "available": track_for(k) is not None and track_for(k).stem == k}
            for k, v in MOODS.items()]
