"""Экспорт: двухпроходная нормализация EBU R128 (−16 LUFS), MP3 320 kbps, WAV, M4B с главами."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from app import config


def loudnorm(inp: Path, out: Path, target: float | None = None) -> dict:
    target = config.TARGET_LUFS if target is None else target
    lra = 11
    tp = -1.5
    p1 = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(inp), "-af", f"loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True, check=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p1.stderr, re.S)
    meas = json.loads(m.group(0))
    af = (f"loudnorm=I={target}:TP={tp}:LRA={lra}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
          f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:"
          f"linear=true:print_format=json")
    p2 = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", str(inp), "-af", af, "-ar", "48000", "-c:a", "pcm_s16le", str(out)],
                        capture_output=True, text=True, check=True)
    m2 = re.search(r"\{[^{}]*\"output_i\"[^{}]*\}", p2.stderr, re.S)
    return json.loads(m2.group(0)) if m2 else meas


def measure_lufs(inp: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(inp), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", tail)
    pk = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return {"integrated_lufs": float(i.group(1)) if i else None, "lra": float(lra.group(1)) if lra else None,
            "true_peak_dbfs": float(pk.group(1)) if pk else None}


def to_mp3(inp: Path, out: Path, title: str = "", artist: str = "", album: str = "", track: str = "") -> None:
    meta = []
    for k, v in (("title", title), ("artist", artist), ("album", album), ("track", track)):
        if v:
            meta += ["-metadata", f"{k}={v}"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(inp), "-c:a", "libmp3lame", "-b:a", config.MP3_BITRATE,
                    "-ar", "48000", *meta, str(out)], check=True)


def concat_wavs(parts: list[Path], out: Path, gap: float = 1.5) -> list[float]:
    """Склейка глав с паузой; возвращает время начала каждой главы."""
    import soundfile as sf
    import numpy as np

    starts, t = [], 0.0
    with sf.SoundFile(str(out), "w", samplerate=48000, channels=2, subtype="PCM_16") as w:
        for i, p in enumerate(parts):
            starts.append(t)
            x, sr = sf.read(str(p), dtype="float32", always_2d=True)
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            w.write(x)
            t += len(x) / sr
            if i < len(parts) - 1:
                w.write(np.zeros((int(gap * 48000), 2), dtype="float32"))
                t += gap
    return starts


def to_m4b(inp: Path, out: Path, chapters: list[tuple[str, float, float]], title: str, author: str,
           cover: Path | None = None) -> None:
    meta = out.with_suffix(".ffmeta")
    lines = [";FFMETADATA1", f"title={_esc(title)}", f"artist={_esc(author)}", f"album={_esc(title)}", "genre=Audiobook"]
    for name, s, e in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(s * 1000)}", f"END={int(e * 1000)}", f"title={_esc(name)}"]
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(inp), "-i", str(meta)]
    if cover and cover.exists():
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    else:
        cmd += ["-map", "0:a"]
    cmd += ["-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-f", "mp4", str(out)]
    subprocess.run(cmd, check=True)
    meta.unlink(missing_ok=True)


def _esc(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s or "")
