"""Процедурная генерация библиотеки фоновой музыки (CC0 — создано этим скриптом, без сэмплов).

Шесть настроений: calm, tension, action, sadness, romance, light. Каждый трек — бесшовный луп.
Запуск: python scripts/generate_music.py  → music/<mood>.mp3
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "music"
rng = np.random.default_rng(7)

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name: str) -> float:
    n, o = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[n] + 12 * (o + 1) - 69) / 12)


def t_arr(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # one-pole; длина треков небольшая
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def lp_fast(x: np.ndarray, cutoff: float) -> np.ndarray:
    # двухпроходный FIR-приближённый lowpass через свёртку (быстрее python-цикла)
    n = int(SR / cutoff)
    n = max(3, n | 1)
    k = np.hanning(n)
    k /= k.sum()
    return np.convolve(x, k, mode="same")


def env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na:
        e[:na] = np.linspace(0, 1, na)
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


def pad_note(f: float, dur: float, bright: float = 1500, detune: float = 0.004) -> np.ndarray:
    t = t_arr(dur)
    x = np.zeros_like(t)
    for d in (-detune, 0, detune):
        ph = 2 * np.pi * f * (1 + d) * t
        # мягкая «пила» из 6 гармоник
        for h in range(1, 7):
            x += np.sin(h * ph + rng.uniform(0, 6.28)) / (h ** 1.3)
    x = lp_fast(x, bright)
    return x * env(len(t), min(1.2, dur / 3), min(1.2, dur / 3))


def pluck(f: float, dur: float, decay: float = 3.0, harmonics: int = 5) -> np.ndarray:
    t = t_arr(dur)
    x = sum(np.sin(2 * np.pi * f * h * t) * (0.6 ** (h - 1)) * np.exp(-decay * h * t) for h in range(1, harmonics + 1))
    return x * env(len(t), 0.004, 0.05)


def bell(f: float, dur: float) -> np.ndarray:
    t = t_arr(dur)
    x = np.sin(2 * np.pi * f * t) * np.exp(-2.5 * t) + 0.4 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-4 * t)
    return x * env(len(t), 0.003, 0.05)


def kick(dur: float = 0.35) -> np.ndarray:
    t = t_arr(dur)
    f = 50 + 90 * np.exp(-25 * t)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-9 * t)


def hat(dur: float = 0.06) -> np.ndarray:
    t = t_arr(dur)
    n = rng.standard_normal(len(t))
    n = n - lp_fast(n, 6000)
    return n * np.exp(-60 * t) * 0.5


def snare(dur: float = 0.2) -> np.ndarray:
    t = t_arr(dur)
    n = rng.standard_normal(len(t)) * np.exp(-18 * t)
    return 0.6 * n + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-20 * t)


def place(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR) % len(buf)
    end = i + len(x)
    if end <= len(buf):
        buf[i:end] += x * gain
    else:  # заворачиваем хвост в начало — бесшовный луп
        k = len(buf) - i
        buf[i:] += x[:k] * gain
        buf[: end - len(buf)] += x[k:] * gain


def reverb(x: np.ndarray, mix: float = 0.3) -> np.ndarray:
    y = x.copy()
    for d, g in ((0.031, 0.5), (0.047, 0.45), (0.071, 0.4), (0.113, 0.35), (0.167, 0.3), (0.229, 0.25)):
        n = int(d * SR)
        y += g * np.roll(x, n) * mix * 2
    return (1 - mix) * x + mix * lp_fast(y, 4000)


def chord(names: list[str]) -> list[float]:
    return [hz(n) for n in names]


def track_calm(L: float) -> np.ndarray:
    buf = np.zeros(int(L * SR))
    prog = [["C3", "G3", "E4"], ["A2", "E3", "C4"], ["F2", "C3", "A3"], ["G2", "D3", "B3"]]
    bar = L / 8
    for b in range(8):
        for f in chord(prog[b % 4]):
            place(buf, pad_note(f, bar * 1.15, 1200), b * bar, 0.16)
        for k, n in enumerate(["E5", "G5", "C6", "D5"] if b % 2 == 0 else ["A5", "E5"]):
            place(buf, bell(hz(n), 2.5), b * bar + k * bar / 4 + 0.3, 0.06)
    return reverb(buf, 0.35)


def track_tension(L: float) -> np.ndarray:
    buf = np.zeros(int(L * SR))
    t = t_arr(L)
    drone = sum(np.sin(2 * np.pi * hz(n) * t) for n in ("D2", "A2")) * 0.18
    drone *= 0.7 + 0.3 * np.sin(2 * np.pi * t / L * 4)  # медленные волны громкости (целое число на луп)
    buf += lp_fast(drone, 600)
    # кластер малых секунд, тремоло
    trem = 0.5 + 0.5 * np.sin(2 * np.pi * 7 * t)
    cl = sum(np.sin(2 * np.pi * hz(n) * t) for n in ("D4", "D#4")) * trem * 0.04
    cl *= 0.5 + 0.5 * np.sin(2 * np.pi * t / L * 2 - np.pi / 2)
    buf += cl
    beat = 60 / 72
    for k in range(int(L / beat)):  # «сердцебиение»
        place(buf, kick(0.3) * 0.5, k * beat, 0.5)
        place(buf, kick(0.3) * 0.35, k * beat + 0.22, 0.5)
    return reverb(buf, 0.3)


def track_action(L: float) -> np.ndarray:
    buf = np.zeros(int(L * SR))
    bpm = 140
    s16 = 60 / bpm / 4
    steps = int(L / s16)
    bass_seq = ["D2", "D2", "F2", "D2", "C2", "D2", "A#1", "C2"]
    ost = ["D4", "A4", "F4", "A4", "D5", "A4", "F4", "A4"]
    for i in range(steps):
        bar_i = (i // 16) % 4
        shift = [0, 0, -2, 1][bar_i]
        if i % 2 == 0:
            f = hz(bass_seq[(i // 2) % 8]) * 2 ** (shift / 12)
            place(buf, pluck(f, 0.25, 6, 6), i * s16, 0.28)
        place(buf, pluck(hz(ost[i % 8]) * 2 ** (shift / 12), 0.15, 14, 3), i * s16, 0.09)
        if i % 4 == 0:
            place(buf, kick(), i * s16, 0.55)
        if i % 8 == 4:
            place(buf, snare(), i * s16, 0.3)
        place(buf, hat(), i * s16, 0.12 if i % 2 else 0.06)
    return reverb(buf, 0.15)


def track_sadness(L: float) -> np.ndarray:
    buf = np.zeros(int(L * SR))
    prog = [["A2", "E3", "C4"], ["F2", "C3", "A3"], ["C3", "G3", "E4"], ["G2", "D3", "B3"]]
    mel = [["E5", "D5", "C5"], ["C5", "A4"], ["G4", "E5", "D5"], ["B4", "D5"]]
    bar = L / 8
    for b in range(8):
        for f in chord(prog[b % 4]):
            place(buf, pad_note(f, bar * 1.1, 900, 0.003), b * bar, 0.13)
        for k, n in enumerate(mel[b % 4]):
            place(buf, pluck(hz(n), 3.0, 1.2, 4), b * bar + k * bar / len(mel[b % 4]), 0.12)
    return reverb(buf, 0.45)


def track_romance(L: float) -> np.ndarray:
    buf = np.zeros(int(L * SR))
    prog = [["F2", "A3", "C4", "E4"], ["E2", "G3", "B3", "D4"], ["D2", "F3", "A3", "C4"], ["C2", "E3", "G3", "B3"]]
    bar = L / 8
    for b in range(8):
        ch = chord(prog[b % 4])
        for f in ch:
            place(buf, pad_note(f, bar * 1.15, 1400), b * bar, 0.11)
        arp = ch[1:] + [ch[1] * 2, ch[2] * 2, ch[3]]
        for k in range(8):
            place(buf, pluck(arp[k % len(arp)] * 2, 1.4, 2.2, 3), b * bar + k * bar / 8, 0.06)
    return reverb(buf, 0.4)


def track_light(L: float) -> np.ndarray:
    """Лёгкая ироничная тема: пиццикато + «фагот» в басу (подходит для юмористической прозы)."""
    buf = np.zeros(int(L * SR))
    bpm = 112
    e8 = 60 / bpm / 2
    steps = int(L / e8)
    mel = ["G4", None, "B4", "D5", None, "B4", "C5", "A4", "F#4", None, "A4", "C5", None, "A4", "B4", "G4"]
    bass = ["G2", "D3", "G2", "D3", "D2", "A2", "D2", "A2"]
    for i in range(steps):
        n = mel[i % 16]
        if n:
            place(buf, pluck(hz(n), 0.3, 12, 4), i * e8, 0.14)
        if i % 2 == 0:
            place(buf, pluck(hz(bass[(i // 2) % 8]), 0.4, 5, 7), i * e8, 0.2)
        if i % 4 == 2:
            place(buf, hat(0.04), i * e8, 0.05)
    return reverb(buf, 0.2)


TRACKS = {
    "calm": (track_calm, 48.0),
    "tension": (track_tension, 60 / 72 * 48),
    "action": (track_action, 60 / 140 * 4 * 24),
    "sadness": (track_sadness, 48.0),
    "romance": (track_romance, 48.0),
    "light": (track_light, 60 / 112 / 2 * 16 * 6),
}


def stereoize(x: np.ndarray) -> np.ndarray:
    d = int(0.012 * SR)
    left = x
    right = 0.85 * x + 0.15 * np.roll(x, d)
    return np.stack([left, right], axis=1)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (fn, L) in TRACKS.items():
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        x = fn(L)
        x = x - np.mean(x)
        x = x / (np.max(np.abs(x)) + 1e-9) * 0.7
        st = stereoize(x)
        raw = OUT / f"{name}.f32"
        st.astype("<f4").tofile(raw)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw),
                        "-af", "loudnorm=I=-20:TP=-2:LRA=7", "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", "160k",
                        str(OUT / f"{name}.mp3")], check=True)
        raw.unlink()
        print("ok", name, round(L, 1), "s")


if __name__ == "__main__":
    main()
