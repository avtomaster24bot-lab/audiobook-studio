"""Каталог голосов и назначение голосов персонажам.

Базовые бесплатные голоса: Silero v4 (5 русских дикторов, локально, CPU) и Microsoft Edge Neural
(2 русских голоса, онлайн, без ключа). Дополнительные тембры получаются сдвигом высоты тона
(rubberband, без изменения темпа) — так набирается 12+ стабильных голосов. Один персонаж = один голос
на всю книгу. Опционально — голоса ElevenLabs (ELEVENLABS_API_KEY + ELEVENLABS_VOICES).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app import config


@dataclass(frozen=True)
class Voice:
    id: str
    title: str          # человекочитаемое имя
    engine: str         # silero | edge | elevenlabs
    speaker: str        # id диктора в движке
    gender: str         # male | female
    age: str            # child | young | adult | old
    pitch: float = 0.0  # сдвиг тона, полутоны
    rate: float = 1.0   # базовый темп
    description: str = ""
    priority: int = 1   # 2 — нестабильный онлайн-голос, назначается персонажам в последнюю очередь

    def to_dict(self) -> dict:
        return asdict(self)


BASE_VOICES: list[Voice] = [
    Voice("edge-dmitry", "Дмитрий", "edge", "ru-RU-DmitryNeural", "male", "adult", 0, 0.97, "ровный, тёплый (онлайн, бывает недоступен)", 2),
    Voice("edge-svetlana", "Светлана (рассказчица)", "edge", "ru-RU-SvetlanaNeural", "female", "adult", 0, 0.98, "ясный, тёплый, повествовательный"),
    Voice("silero-aidar", "Айдар", "silero", "aidar", "male", "adult", 0, 1.0, "мужской, средний"),
    Voice("silero-eugene", "Евгений", "silero", "eugene", "male", "adult", 0, 1.0, "мужской, мягкий"),
    Voice("silero-baya", "Бая", "silero", "baya", "female", "adult", 0, 1.0, "женский, низковатый"),
    Voice("silero-kseniya", "Ксения", "silero", "kseniya", "female", "adult", 0, 1.0, "женский, звонкий"),
    Voice("silero-xenia", "Ксюша", "silero", "xenia", "female", "young", 0, 1.0, "молодой женский"),
    Voice("silero-aidar-low", "Айдар (бас)", "silero", "aidar", "male", "old", -3.0, 0.94, "низкий, грузный, начальственный"),
    Voice("silero-eugene-high", "Евгений (юноша)", "silero", "eugene", "male", "young", 2.0, 1.04, "молодой, лёгкий"),
    Voice("edge-dmitry-low", "Дмитрий (басовитый)", "edge", "ru-RU-DmitryNeural", "male", "old", -2.5, 0.95, "густой, солидный (онлайн)", 2),
    Voice("silero-eugene-low", "Евгений (хрипловатый)", "silero", "eugene", "male", "old", -2.0, 0.96, "простонародный, грубоватый"),
    Voice("silero-baya-low", "Бая (пожилая)", "silero", "baya", "female", "old", -2.0, 0.93, "пожилая женщина"),
    Voice("silero-xenia-high", "Ксюша (ребёнок)", "silero", "xenia", "female", "child", 2.5, 1.06, "детский голос"),
    Voice("edge-svetlana-low", "Светлана (грудной)", "edge", "ru-RU-SvetlanaNeural", "female", "adult", -2.0, 0.97, "грудной, бархатный"),
    Voice("silero-aidar-high", "Айдар (тенор)", "silero", "aidar", "male", "young", 1.5, 1.03, "звонкий тенор"),
]


def _elevenlabs_voices() -> list[Voice]:
    """ELEVENLABS_VOICES="Имя:voice_id:male,Имя2:voice_id2:female"."""
    import os

    out = []
    if not config.ELEVENLABS_API_KEY:
        return out
    for item in os.environ.get("ELEVENLABS_VOICES", "").split(","):
        parts = item.strip().split(":")
        if len(parts) >= 2:
            g = parts[2] if len(parts) > 2 else "male"
            out.append(Voice(f"eleven-{parts[1]}", f"{parts[0]} (ElevenLabs)", "elevenlabs", parts[1], g, "adult"))
    return out


def all_voices() -> list[Voice]:
    vs = [v for v in BASE_VOICES if v.engine in config.TTS_ENGINES] + _elevenlabs_voices()
    return vs


def get_voice(vid: str) -> Voice:
    for v in all_voices():
        if v.id == vid:
            return v
    for v in BASE_VOICES:  # движок мог быть отключён — подставим ближайший
        if v.id == vid:
            return fallback_for(v)
    raise KeyError(vid)


def fallback_for(v: Voice) -> Voice:
    pool = [x for x in all_voices() if x.gender == v.gender] or all_voices()
    return pool[0]


def narrator_voice() -> Voice:
    import os

    for vid in (os.environ.get("NARRATOR_VOICE", "edge-svetlana"), "edge-svetlana", "edge-dmitry", "silero-eugene", "silero-aidar"):
        try:
            v = get_voice(vid)
            if v.id == vid:
                return v
        except KeyError:
            pass
    return all_voices()[0]


AGE_ORDER = {"child": 0, "young": 1, "adult": 2, "old": 3}


def assign_voices(characters: list[dict], narrator: str | None = None, overrides: dict | None = None) -> dict[str, str]:
    """Назначает голоса: главные персонажи — самые отличающиеся тембры; без повторов, пока хватает голосов."""
    overrides = overrides or {}
    narr = narrator or narrator_voice().id
    mapping = {"narrator": narr}
    used = {narr}
    voices = all_voices()
    # рассказчику — отдельный голос: его же тембр (с другим тоном) персонажам не даём в первую очередь
    narr_speaker = get_voice(narr).speaker
    for ch in characters:
        name = ch["name"]
        if name in overrides:
            mapping[name] = overrides[name]
            used.add(overrides[name])
            continue
        gender = ch.get("gender") if ch.get("gender") in ("male", "female") else "male"
        age = ch.get("age") or "adult"
        cands = [v for v in voices if v.gender == gender and v.id not in used]
        if not cands:
            cands = [v for v in voices if v.id not in used] or [v for v in voices if v.id != narr]
        cands.sort(key=lambda v: (v.priority, v.speaker == narr_speaker, abs(AGE_ORDER.get(v.age, 2) - AGE_ORDER.get(age, 2)), v.pitch != 0))
        pick = cands[0]
        mapping[name] = pick.id
        used.add(pick.id)
    return mapping
