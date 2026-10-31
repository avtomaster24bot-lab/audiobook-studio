"""Настройки сервиса. Все значения берутся из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
MUSIC_DIR = Path(_env("MUSIC_DIR", str(BASE_DIR / "music")))
STATIC_DIR = BASE_DIR / "static"

# LLM (любой OpenAI-совместимый API: OpenAI, OpenRouter, DeepSeek, Ollama, vLLM, LM Studio…)
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")
LLM_ENABLED = bool(OPENAI_API_KEY) and _env("LLM_DISABLED", "0") != "1"

# TTS
TTS_ENGINES = [e.strip() for e in _env("TTS_ENGINES", "silero,edge").split(",") if e.strip()]
ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
ELEVENLABS_MODEL = _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
SILERO_SAMPLE_RATE = int(_env("SILERO_SAMPLE_RATE", "48000"))
TORCH_THREADS = int(_env("TORCH_THREADS", "4"))

# Аудио
TARGET_LUFS = float(_env("TARGET_LUFS", "-16"))
MUSIC_GAIN_DB = float(_env("MUSIC_GAIN_DB", "-12"))  # уровень музыки в паузах относительно голоса
MP3_BITRATE = _env("MP3_BITRATE", "320k")

# Источники
USER_AGENT = _env(
    "CRAWLER_USER_AGENT",
    "AudiobookStudioBot/0.1 (+https://github.com/avtomaster24bot-lab/audiobook-studio)",
)
CRAWL_DELAY_SEC = float(_env("CRAWL_DELAY_SEC", "1.0"))
MAX_WORDS = int(_env("MAX_WORDS", "500000"))

# Очередь
WORKERS = int(_env("WORKERS", "1"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "jobs").mkdir(exist_ok=True)
(DATA_DIR / "cache").mkdir(exist_ok=True)
(DATA_DIR / "previews").mkdir(exist_ok=True)
