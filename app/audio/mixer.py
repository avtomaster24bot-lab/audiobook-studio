"""Сборка главы: дорожка голосов + музыкальная подложка с кроссфейдами + ducking (sidechaincompress).

Уровни (по ТЗ): голос ≈ 0 dB (опорный), музыка в паузах ≈ −12 dB, под речью ≈ −18…−24 dB.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app import config
from app.audio.music import track_for

SR = 48000
VOICE_TARGET_DBFS = -20.0   # RMS активной речи каждого клипа (выравнивание разных движков)
LEAD_IN = 2.5               # музыка до первой фразы, с
TAIL = 3.5                  # музыка после последней фразы, с
XFADE = 3.0                 # кроссфейд между сценами, с


@dataclass
class Clip:
    path: Path
    pause_after: float
    gain_db: float = 0.0
    segment_index: int = 0


def _rms_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    frame = SR // 20
    n = len(x) // frame
    if n == 0:
        return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
    fr = x[: n * frame].reshape(n, frame)
    r = np.sqrt(np.mean(fr ** 2, axis=1))
    active = r[r > np.max(r) * 0.1] if np.max(r) > 0 else r
    return 20 * np.log10(np.sqrt(np.mean(active ** 2)) + 1e-9)


def _read_mono(p: Path) -> np.ndarray:
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        # редкий случай — ресемплим через ffmpeg
        tmp = p.with_suffix(".rs.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-ar", str(SR), "-ac", "1", str(tmp)], check=True)
        x, _ = sf.read(str(tmp), dtype="float32")
    return x


def build_voice_track(clips: list[Clip], out: Path) -> tuple[float, dict[int, float]]:
    """Склеивает клипы с паузами, выравнивает громкость. Возвращает (длительность, {segment_index: start_sec})."""
    parts = [np.zeros(int(LEAD_IN * SR), dtype="float32")]
    t = LEAD_IN
    starts: dict[int, float] = {}
    for c in clips:
        x = _read_mono(c.path)
        g = VOICE_TARGET_DBFS - _rms_db(x) + c.gain_db
        x = x * (10 ** (g / 20))
        starts.setdefault(c.segment_index, t)
        parts.append(x)
        t += len(x) / SR
        pause = np.zeros(int(c.pause_after * SR), dtype="float32")
        parts.append(pause)
        t += c.pause_after
    parts.append(np.zeros(int(TAIL * SR), dtype="float32"))
    y = np.concatenate(parts)
    peak = np.max(np.abs(y)) if len(y) else 1
    if peak > 0.98:
        y = y / peak * 0.98
    sf.write(str(out), y, SR, subtype="PCM_16")
    return len(y) / SR, starts


def _load_music(p: Path) -> np.ndarray:
    tmp = p.parent / f".{p.stem}.48k.f32"
    cache = config.DATA_DIR / "cache" / f"music-{p.stem}-{int(p.stat().st_mtime)}.npy"
    if cache.exists():
        return np.load(cache)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-ar", str(SR), "-ac", "2", "-f", "f32le", str(tmp)],
                   check=True)
    x = np.fromfile(tmp, dtype="<f4").reshape(-1, 2)
    tmp.unlink(missing_ok=True)
    # убираем тишину энкодера на краях и делаем шов лупа мягким
    x = x[2048:-2048] if len(x) > 8192 else x
    np.save(cache, x)
    return x


def _loop_to(x: np.ndarray, n: int) -> np.ndarray:
    """Бесшовный повтор лупа: каждый повтор стыкуется кроссфейдом 0.5 с."""
    xf = int(0.5 * SR)
    if len(x) <= xf * 2:
        return np.resize(x, (n, 2))
    out = x.copy()
    fade_in = np.linspace(0, 1, xf)[:, None]
    while len(out) < n:
        head = x[:xf] * fade_in + out[-xf:] * (1 - fade_in)
        out = np.concatenate([out[:-xf], head, x[xf:]])
    return out[:n]


def build_music_bed(scenes: list[tuple[float, float, str]], total: float, out: Path) -> list[dict]:
    """scenes: [(start_sec, end_sec, mood)] → стерео-подложка с равномощными кроссфейдами между сценами."""
    n_total = int(total * SR)
    bed = np.zeros((n_total, 2), dtype="float32")
    xf = int(XFADE * SR)
    used = []
    offsets: dict[str, int] = {}
    for i, (s, e, mood) in enumerate(scenes):
        p = track_for(mood)
        if p is None:
            continue
        src = _load_music(p)
        a = max(0, int(s * SR) - (xf // 2 if i > 0 else 0))
        b = min(n_total, int(e * SR) + (xf // 2 if i < len(scenes) - 1 else 0))
        if b <= a:
            continue
        # продолжаем трек того же настроения с места, где он закончился (без «перезапуска» темы)
        off = offsets.get(mood, 0)
        seg = _loop_to(np.roll(src, -off, axis=0), b - a)
        offsets[mood] = (off + (b - a)) % len(src)
        env = np.ones(b - a, dtype="float32")
        k = min(xf, (b - a) // 2)
        if i > 0:
            env[:k] = np.sin(np.linspace(0, np.pi / 2, k)) ** 2
        else:
            k0 = min(int(2.0 * SR), (b - a) // 2)
            env[:k0] = np.linspace(0, 1, k0)
        if i < len(scenes) - 1:
            env[-k:] = np.cos(np.linspace(0, np.pi / 2, k)) ** 2
        else:
            k1 = min(int(TAIL * SR), (b - a) // 2)
            env[-k1:] = np.linspace(1, 0, k1)
        bed[a:b] += seg * env[:, None]
        used.append({"start": round(s, 2), "end": round(e, 2), "mood": mood, "track": p.name})
    sf.write(str(out), bed, SR, subtype="PCM_16")
    return used


def duck_and_mix(voice: Path, music: Path, out_mix: Path, out_music_ducked: Path | None = None,
                 music_gain_db: float | None = None) -> None:
    """ffmpeg: музыка приглушается сайдчейн-компрессором по голосу, затем сведение.

    loudnorm применяется позже, на этапе экспорта (двухпроходно).
    """
    g = config.MUSIC_GAIN_DB if music_gain_db is None else music_gain_db
    fc = (
        f"[0:a]aformat=channel_layouts=stereo,asplit=2[v][sc];"
        f"[1:a]aformat=channel_layouts=stereo,volume={g}dB[m];"
        f"[m][sc]sidechaincompress=threshold=0.015:ratio=3:attack=25:release=450:knee=3:makeup=1:link=maximum:detection=rms[md];"
        f"[md]asplit=2[md1][md2];"
        f"[v][md1]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95:level=disabled[mix]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-i", str(music), "-filter_complex", fc,
           "-map", "[mix]", "-ar", str(SR), "-c:a", "pcm_s16le", str(out_mix)]
    if out_music_ducked:
        cmd += ["-map", "[md2]", "-ar", str(SR), "-c:a", "pcm_s16le", str(out_music_ducked)]
    else:
        cmd[cmd.index("-filter_complex") + 1] = fc.replace("[md]asplit=2[md1][md2];", "").replace("[md1]", "[md]")
    subprocess.run(cmd, check=True)


def ducking_report(voice: Path, music_ducked: Path, music_raw_gain_db: float | None = None) -> dict:
    """Метрика «музыка не перекрывает голос»: уровни по кадрам 50 мс на участках речи и в паузах."""
    v, _ = sf.read(str(voice), dtype="float32", always_2d=True)
    m, _ = sf.read(str(music_ducked), dtype="float32", always_2d=True)
    n = min(len(v), len(m))
    v, m = v[:n].mean(axis=1), m[:n].mean(axis=1)
    frame = SR // 20
    k = n // frame
    vr = 20 * np.log10(np.sqrt(np.mean(v[: k * frame].reshape(k, frame) ** 2, axis=1)) + 1e-9)
    mr = 20 * np.log10(np.sqrt(np.mean(m[: k * frame].reshape(k, frame) ** 2, axis=1)) + 1e-9)
    speech = vr > -45
    gaps = (vr < -70) & (mr > -80)
    rep = {
        "speech_frames": int(speech.sum()),
        "gap_frames": int(gaps.sum()),
        "voice_rms_db_speech": round(float(np.median(vr[speech])), 1) if speech.any() else None,
        "music_rms_db_under_speech": round(float(np.median(mr[speech])), 1) if speech.any() else None,
        "music_rms_db_in_gaps": round(float(np.median(mr[gaps])), 1) if gaps.any() else None,
    }
    if speech.any():
        rep["voice_minus_music_db"] = round(rep["voice_rms_db_speech"] - rep["music_rms_db_under_speech"], 1)
        rep["share_speech_frames_music_within_12db"] = round(float(np.mean((vr[speech] - mr[speech]) < 12)), 3)
    if gaps.any() and speech.any():
        rep["ducking_depth_db"] = round(rep["music_rms_db_in_gaps"] - rep["music_rms_db_under_speech"], 1)
    return rep


def write_json(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
