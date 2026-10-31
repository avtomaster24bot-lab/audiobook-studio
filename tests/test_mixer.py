"""Ducking: музыка под речью должна быть заметно тише, чем в паузах."""
import numpy as np
import soundfile as sf

from app.audio import mixer


def test_sidechain_ducking(tmp_path):
    sr = mixer.SR
    t = np.arange(sr * 8) / sr
    voice = np.zeros_like(t)
    speech = (t > 2) & (t < 5)
    voice[speech] = 0.3 * np.sin(2 * np.pi * 220 * t[speech]) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[speech]))
    music = 0.3 * np.sin(2 * np.pi * 110 * t)
    vp, mp = tmp_path / "v.wav", tmp_path / "m.wav"
    sf.write(vp, voice.astype("float32"), sr)
    sf.write(mp, np.stack([music, music], 1).astype("float32"), sr)
    out, ducked = tmp_path / "mix.wav", tmp_path / "md.wav"
    mixer.duck_and_mix(vp, mp, out, ducked, music_gain_db=-12)
    rep = mixer.ducking_report(vp, ducked)
    assert rep["ducking_depth_db"] >= 5
    assert rep["voice_minus_music_db"] >= 15
