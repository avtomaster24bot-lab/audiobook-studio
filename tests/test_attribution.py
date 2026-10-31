from pathlib import Path

from app.text.analyzer import analyze
from app.models import Book, Chapter
from app.text.attribution import attribute, merge_false_remarks
from app.text.structure import segment_chapter

FIX = Path(__file__).parent / "fixtures" / "hameleon.txt"

# эталон для «Хамелеона» (индексы реплик по порядку)
GOLD = [
    "Хрюкин", "Хрюкин", "Елдырин", "Очумелов", "Очумелов", "Хрюкин", "Хрюкин", "Очумелов", "Очумелов", "Очумелов",
    "Голос из толпы", "Очумелов", "Очумелов", "Голос из толпы", "Хрюкин", "Очумелов", "Елдырин", "Елдырин",
    "Очумелов", "Елдырин", "Очумелов", "Елдырин", "Елдырин", "Голос из толпы", "Очумелов", "Елдырин", "Прохор",
    "Очумелов", "Очумелов", "Прохор", "Прохор", "Очумелов", "Очумелов", "Прохор", "Очумелов", "Очумелов",
]


def _run(text):
    segs = merge_false_remarks(segment_chapter(text))
    chars, [segs] = attribute([segs])
    return chars, segs


def test_explicit_attribution_simple():
    text = ("— Куда ты? — спросил Иван.\n"
            "— Домой, — ответила Мария.\n"
            "— Подожди меня! — крикнул Иван.\n")
    chars, segs = _run(text)
    d = [s.speaker for s in segs if s.kind == "dialogue"]
    assert d == ["Иван", "Мария", "Иван"]
    genders = {c.name: c.gender for c in chars}
    assert genders["Мария"] == "female" and genders["Иван"] == "male"


def test_inflected_names_merge_and_roles():
    text = ("Через площадь идёт надзиратель Очумелов.\n"
            "— Кто кричал? — спрашивает Очумелов.\n"
            "— Я, — говорит надзиратель.\n"
            "— Хрюкину палец укусили, — говорит Елдырин.\n")
    chars, segs = _run(text)
    names = {c.name for c in chars}
    assert "Очумелов" in names and "Надзиратель" not in names
    d = [s.speaker for s in segs if s.kind == "dialogue"]
    assert d == ["Очумелов", "Очумелов", "Елдырин"]


def test_perception_verb_is_not_speaker():
    text = "— Так ты кусаться, окаянная? — слышит вдруг Очумелов.\n"
    _, segs = _run(text)
    assert segs[0].speaker != "Очумелов"


def test_vocative_reply():
    text = ("— Эй, Прохор! Поди-ка сюда! Ваша собака? — кричит Елдырин.\n"
            "— Выдумал! Этаких у нас отродясь не бывало!\n")
    _, segs = _run(text)
    d = [s.speaker for s in segs if s.kind == "dialogue"]
    assert d[-1] == "Прохор"


def test_hameleon_rule_accuracy_baseline():
    """Rule-based без LLM: на «Хамелеоне» (сложная сценка с толпой) — не ниже 70%.
    Целевые ≥90% по ТЗ достигаются LLM-режимом (OPENAI_API_KEY)."""
    _, segs = _run(FIX.read_text())
    pred = [s.speaker for s in segs if s.kind == "dialogue"]
    assert len(pred) == len(GOLD)
    acc = sum(p == g for p, g in zip(pred, GOLD)) / len(GOLD)
    assert acc >= 0.70, acc


def test_analyze_produces_scenes_and_emotions():
    a = analyze(Book(title="Хамелеон", chapters=[Chapter("Хамелеон", FIX.read_text())]), use_llm=False)
    ch = a["chapters"][0]
    assert a["method"] == "rules"
    assert ch["scenes"] and ch["scenes"][0]["start"] == 0
    emos = {s["emotion"] for s in ch["segments"]}
    assert "anger" in emos and len(emos) >= 3
