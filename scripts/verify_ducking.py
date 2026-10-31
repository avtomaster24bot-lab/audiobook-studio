"""Независимая проверка ducking и громкости готового демо.

    python scripts/verify_ducking.py data/jobs/<job>/work docs/demo/book.mp3 --svg docs/demo/ducking.svg

Берёт стемы (голос `ch000_voice.wav` и приглушённая музыка `ch000_music_ducked.wav`), считает уровни
по кадрам 50 мс и печатает распределение «голос − музыка» на речи, глубину ducking в паузах,
а также интегральную громкость итогового MP3 (EBU R128 через ffmpeg). Опционально рисует SVG-график.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

FRAME = 0.05


def levels(p: Path) -> tuple[np.ndarray, int]:
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    f = int(sr * FRAME)
    k = len(x) // f
    r = np.sqrt(np.mean(x[: k * f].reshape(k, f) ** 2, axis=1))
    return 20 * np.log10(r + 1e-9), sr


def ebur128(p: Path) -> dict:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    tail = out[out.rfind("Summary:"):]
    g = lambda pat: float(re.search(pat, tail).group(1))  # noqa: E731
    return {"integrated_lufs": g(r"I:\s+(-?[\d.]+) LUFS"), "lra_lu": g(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbfs": g(r"Peak:\s+(-?[\d.]+) dBFS")}


def svg(vr: np.ndarray, mr: np.ndarray, out: Path, seconds: float = 90.0) -> None:
    n = min(len(vr), len(mr), int(seconds / FRAME))
    w, h, lo, hi = 1000, 260, -70.0, -10.0
    def pts(a):
        a = np.convolve(np.clip(a[:n], lo, hi), np.ones(3) / 3, mode="same")
        return " ".join(f"{i * w / n:.1f},{h - (v - lo) / (hi - lo) * h:.1f}" for i, v in enumerate(a))
    grid = "".join(f'<line x1="0" x2="{w}" y1="{h - (d - lo) / (hi - lo) * h:.0f}" y2="{h - (d - lo) / (hi - lo) * h:.0f}" '
                   f'stroke="#333" stroke-dasharray="4 4"/><text x="4" y="{h - (d - lo) / (hi - lo) * h - 3:.0f}" '
                   f'fill="#888" font-size="11">{d} dBFS</text>' for d in (-20, -30, -40, -50, -60))
    ticks = "".join(f'<text x="{t / seconds * w:.0f}" y="{h + 14}" fill="#888" font-size="11">{t} с</text>'
                    for t in range(0, int(seconds) + 1, 10))
    out.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h + 20}" font-family="sans-serif">'
        f'<rect width="{w}" height="{h}" fill="#111"/>{grid}{ticks}'
        f'<polyline fill="none" stroke="#4fc3f7" stroke-width="1.2" points="{pts(vr)}"/>'
        f'<polyline fill="none" stroke="#ffb74d" stroke-width="1.6" points="{pts(mr)}"/>'
        f'<text x="{w - 260}" y="18" fill="#4fc3f7" font-size="13">голос (RMS, 50 мс)</text>'
        f'<text x="{w - 260}" y="36" fill="#ffb74d" font-size="13">музыка после ducking</text></svg>')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("mp3")
    ap.add_argument("--svg")
    a = ap.parse_args()
    wd = Path(a.work_dir)
    vr, _ = levels(wd / "ch000_voice.wav")
    mr, _ = levels(wd / "ch000_music_ducked.wav")
    n = min(len(vr), len(mr))
    vr, mr = vr[:n], mr[:n]
    speech = vr > -35          # уверенная речь
    gaps = (vr < -70) & (mr > -80)
    d = vr[speech] - mr[speech]
    rep = {
        "frames_speech": int(speech.sum()), "frames_gaps": int(gaps.sum()),
        "voice_median_dbfs": round(float(np.median(vr[speech])), 1),
        "music_under_speech_median_dbfs": round(float(np.median(mr[speech])), 1),
        "music_in_gaps_median_dbfs": round(float(np.median(mr[gaps])), 1),
        "voice_minus_music_db": {"p5": round(float(np.percentile(d, 5)), 1), "p50": round(float(np.median(d)), 1),
                                 "p95": round(float(np.percentile(d, 95)), 1)},
        "share_speech_frames_music_louder_than_voice": round(float(np.mean(d < 0)), 4),
        "share_speech_frames_gap_below_10db": round(float(np.mean(d < 10)), 4),
    }
    rep["ducking_depth_db"] = round(rep["music_in_gaps_median_dbfs"] - rep["music_under_speech_median_dbfs"], 1)
    rep["music_in_gaps_vs_voice_db"] = round(rep["music_in_gaps_median_dbfs"] - rep["voice_median_dbfs"], 1)
    rep["final_mp3"] = ebur128(Path(a.mp3))
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if a.svg:
        svg(vr, mr, Path(a.svg))


if __name__ == "__main__":
    main()
