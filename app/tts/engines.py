"""Движки синтеза: Silero (локально), Edge TTS (онлайн, бесплатно), ElevenLabs (по ключу).

Каждый сегмент синтезируется в WAV 48 кГц моно и кэшируется на диске по хэшу
(текст + голос + просодия) — повторная генерация главы не тратит ресурсы.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import subprocess
import threading
from pathlib import Path

from app import config
from app.tts.voices import Voice, fallback_for

log = logging.getLogger(__name__)
SR = 48000
CACHE = config.DATA_DIR / "cache" / "tts"
CACHE.mkdir(parents=True, exist_ok=True)

_silero_model = None
_silero_lock = threading.Lock()

# эмоция → (темп, сдвиг тона в полутонах, громкость дБ)
PROSODY = {
    "neutral": (1.0, 0.0, 0.0),
    "joy": (1.06, 0.8, 0.5),
    "anger": (1.07, 0.6, 2.0),
    "fear": (1.10, 1.0, -1.0),
    "sadness": (0.90, -0.7, -2.0),
    "surprise": (1.03, 1.2, 1.0),
    "tenderness": (0.93, 0.3, -2.0),
    "irony": (0.97, 0.4, 0.0),
    "excitement": (1.10, 0.9, 1.5),
    "whisper": (0.92, -0.3, -6.0),
}


def prosody_for(emotion: str, intensity: float) -> tuple[float, float, float]:
    rate, pitch, vol = PROSODY.get(emotion, PROSODY["neutral"])
    k = max(0.0, min(1.0, intensity)) / 0.6
    k = min(k, 1.4)
    return 1 + (rate - 1) * k, pitch * k, vol * k


def _silero():
    global _silero_model
    if _silero_model is None:
        import torch

        torch.set_num_threads(config.TORCH_THREADS)
        model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru",
                                  trust_repo=True)
        _silero_model = model
    return _silero_model


def numbers_to_words(text: str) -> str:
    try:
        from num2words import num2words
    except ImportError:  # pragma: no cover
        return text
    return re.sub(r"\d+", lambda m: num2words(int(m.group(0)), lang="ru"), text)


def tts_clean(text: str) -> str:
    text = numbers_to_words(text)
    text = text.replace("«", "").replace("»", "").replace("—", ",").replace("…", "...")
    text = re.sub(r"[\[\]{}<>*_#|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[,.\s]+", "", text)
    return text


def chunks(text: str, limit: int = 800) -> list[str]:
    sents = re.split(r"(?<=[.!?…])\s+", text)
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) + 1 > limit and cur:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
        while len(cur) > limit:
            cut = cur.rfind(",", 0, limit)
            cut = cut if cut > 100 else limit
            out.append(cur[: cut + 1])
            cur = cur[cut + 1:].strip()
    if cur:
        out.append(cur)
    return out


def _silero_synth(text: str, speaker: str, out: Path) -> None:
    import numpy as np
    import soundfile as sf

    model = _silero()
    parts = []
    with _silero_lock:
        for ch in chunks(text):
            if not re.search(r"[А-Яа-яЁёA-Za-z]", ch):
                continue
            a = model.apply_tts(text=ch, speaker=speaker, sample_rate=SR, put_accent=True, put_yo=True)
            parts.append(a.numpy())
            parts.append(np.zeros(int(SR * 0.12), dtype="float32"))
    if not parts:
        parts = [np.zeros(int(SR * 0.2), dtype="float32")]
    sf.write(str(out), np.concatenate(parts), SR, subtype="PCM_16")


def _edge_synth(text: str, speaker: str, out: Path) -> None:
    import edge_tts

    mp3 = out.with_suffix(".edge.mp3")

    async def run():
        await edge_tts.Communicate(text, speaker).save(str(mp3))

    last = None
    for attempt in range(4):
        try:
            asyncio.run(run())
            if mp3.exists() and mp3.stat().st_size > 500:
                break
        except Exception as e:  # сетевые сбои / «No audio was received»
            last = e
            import time
            time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"edge-tts failed: {last}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(out)], check=True)
    mp3.unlink(missing_ok=True)


def _eleven_synth(text: str, voice_id: str, out: Path) -> None:
    import httpx

    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
        json={"text": text, "model_id": config.ELEVENLABS_MODEL},
        timeout=120,
    )
    r.raise_for_status()
    mp3 = out.with_suffix(".11.mp3")
    mp3.write_bytes(r.content)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(out)], check=True)
    mp3.unlink(missing_ok=True)


def _post(raw: Path, out: Path, pitch_st: float, rate: float, vol_db: float) -> None:
    """Сдвиг тона/темпа (rubberband сохраняет формантный характер речи лучше, чем asetrate), обрезка тишины."""
    filters = ["silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05"]
    if abs(pitch_st) > 0.05 or abs(rate - 1) > 0.01:
        filters.append(f"rubberband=pitch={2 ** (pitch_st / 12):.4f}:tempo={rate:.4f}:formant=preserved:pitchq=quality")
    filters.append("areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse")
    if abs(vol_db) > 0.05:
        filters.append(f"volume={vol_db:.1f}dB")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", ",".join(filters),
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], check=True)


def synthesize(text: str, voice: Voice, emotion: str = "neutral", intensity: float = 0.3,
               rate_mul: float = 1.0, pitch_add: float = 0.0) -> Path:
    clean = tts_clean(text)
    rate, epitch, _vol = prosody_for(emotion, intensity)
    vol = 0.0  # громкость эмоции применяет микшер после выравнивания громкости клипов
    rate *= voice.rate * rate_mul
    pitch = voice.pitch + epitch + pitch_add
    key = hashlib.sha1(f"{clean}|{voice.engine}|{voice.speaker}|{pitch:.2f}|{rate:.3f}|{vol:.1f}".encode()).hexdigest()
    out = CACHE / f"{key}.wav"
    if out.exists():
        return out
    raw_key = hashlib.sha1(f"{clean}|{voice.engine}|{voice.speaker}".encode()).hexdigest()
    raw = CACHE / f"raw-{raw_key}.wav"
    if not raw.exists():
        engine = voice.engine
        try:
            if engine == "silero":
                _silero_synth(clean, voice.speaker, raw)
            elif engine == "edge":
                _edge_synth(clean, voice.speaker, raw)
            elif engine == "elevenlabs":
                _eleven_synth(clean, voice.speaker, raw)
            else:
                raise ValueError(engine)
        except Exception as e:
            if engine == "silero":
                raise
            log.warning("TTS engine %s failed (%s), fallback to silero", engine, e)
            fb = fallback_for(voice) if voice.engine != "silero" else voice
            if fb.engine != "silero":
                from app.tts.voices import BASE_VOICES
                fb = next(v for v in BASE_VOICES if v.engine == "silero" and v.gender == voice.gender)
            raw = CACHE / f"raw-fb-{raw_key}.wav"
            _silero_synth(clean, fb.speaker, raw)
            _post(raw, out.with_suffix(".fb.wav"), pitch, rate, vol)
            return out.with_suffix(".fb.wav")  # фоллбэк не кэшируем под ключом основного движка
    _post(raw, out, pitch, rate, vol)
    return out


_health: dict[str, tuple[float, bool]] = {}


def engine_voice_ok(voice: Voice, ttl: float = 600) -> bool:
    """Проверка доступности онлайн-голоса перед задачей (чтобы не менять голос посреди книги)."""
    import time
    import tempfile

    if voice.engine == "silero":
        return True
    key = f"{voice.engine}:{voice.speaker}"
    now = time.time()
    if key in _health and now - _health[key][0] < ttl:
        return _health[key][1]
    ok = False
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "probe.wav"
        try:
            if voice.engine == "edge":
                _edge_synth("Проверка связи.", voice.speaker, out)
            elif voice.engine == "elevenlabs":
                ok = bool(config.ELEVENLABS_API_KEY)
                _health[key] = (now, ok)
                return ok
            ok = out.exists() and out.stat().st_size > 1000
        except Exception as e:
            log.warning("voice %s unavailable: %s", key, e)
            ok = False
    _health[key] = (now, ok)
    return ok
