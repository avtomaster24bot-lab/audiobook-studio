"""LLM-слой через любой OpenAI-совместимый API (OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL).

Если ключ не задан — вызывающий код использует rule-based фоллбэк.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app import config

log = logging.getLogger(__name__)

EMOTIONS = ["neutral", "joy", "anger", "fear", "sadness", "surprise", "tenderness", "irony", "excitement", "whisper"]
MOODS = ["calm", "tension", "action", "sadness", "romance", "light"]


def enabled() -> bool:
    return config.LLM_ENABLED


def chat_json(system: str, user: str, max_tokens: int = 4000) -> dict | None:
    if not enabled():
        return None
    url = config.OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
    body = {
        "model": config.OPENAI_MODEL,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    for attempt in range(3):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=180)
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format", None)
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            return json.loads(m.group(0) if m else content)
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as e:
            log.warning("LLM attempt %s failed: %s", attempt + 1, e)
    return None


def parse_book_query(query: str) -> dict | None:
    return chat_json(
        "Ты помогаешь найти книгу. Верни JSON {\"title\": ..., \"author\": ...} — каноническое название "
        "произведения и полное имя автора на языке оригинала (для русских книг — по-русски). "
        "Если автор не указан, но произведение известно — подставь автора.",
        query, max_tokens=200,
    )


ANALYZE_SYSTEM = f"""Ты — режиссёр аудиоспектакля. На вход — пронумерованные сегменты главы:
N (narration, авторская речь) и D (dialogue, реплика). Для каждой реплики D определи говорящего,
для каждого сегмента — эмоцию и интенсивность. Определи персонажей и сцены.
Верни строго JSON:
{{
 "characters": [{{"name": "каноническое имя в именительном падеже", "aliases": ["формы/прозвища"],
   "gender": "male|female|unknown", "age": "child|young|adult|old", "traits": "2-4 слова о характере/голосе"}}],
 "segments": [{{"i": номер, "speaker": "имя персонажа или narrator", "emotion": один из {EMOTIONS},
   "intensity": 0..1}}],
 "scenes": [{{"start": номер первого сегмента, "mood": один из {MOODS}}}]
}}
Правила: авторская речь (N) всегда speaker=narrator. Не придумывай персонажей, которых нет в тексте;
безымянных говорящих называй по роли («Голос из толпы», «Повар»). Для реплик без атрибуции используй контекст
и чередование собеседников. Точность атрибуции важнее всего."""


def analyze_segments(numbered: list[dict], known_characters: list[dict]) -> dict | None:
    lines = [f"{s['i']} {s['kind'][0].upper()}: {s['text']}" for s in numbered]
    user = ""
    if known_characters:
        user += "Уже известные персонажи: " + json.dumps(known_characters, ensure_ascii=False) + "\n\n"
    user += "\n".join(lines)
    return chat_json(ANALYZE_SYSTEM, user, max_tokens=6000)
