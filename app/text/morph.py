"""Морфология имён: нормальная форма и пол (pymorphy3, если установлен; иначе эвристики по окончаниям)."""
from __future__ import annotations

import functools
import re

try:
    import pymorphy3

    _morph = pymorphy3.MorphAnalyzer()
except Exception:  # pragma: no cover
    _morph = None

NAME_TAGS = {"Name", "Surn", "Patr"}


@functools.lru_cache(maxsize=20000)
def is_person_name(word: str) -> bool:
    if not word or not word[0].isupper():
        return False
    if _morph is None:
        return bool(re.match(r"^[А-ЯЁ][а-яё]+(ов|ев|ин|ын|ский|цкий|ова|ева|ина|ая)?[а-яё]*$", word))
    for p in _morph.parse(word)[:3]:
        if NAME_TAGS & set(p.tag.grammemes) and p.score > 0.05:
            return True
    return False


@functools.lru_cache(maxsize=20000)
def normal_name(word: str) -> str:
    """Хрюкину / Хрюкиным → Хрюкин; Ивана → Иван; Очумелов → Очумелов."""
    if _morph is not None:
        parses = [p for p in _morph.parse(word) if NAME_TAGS & set(p.tag.grammemes)]
        # слово уже в именительном падеже
        for p in parses:
            if "nomn" in p.tag and "sing" in p.tag:
                return word
        for p in parses:
            inf = p.inflect({"nomn", "sing"})
            if inf:
                w = inf.word
                return w[:1].upper() + w[1:]
    w = word
    for suf, rep in (("ым", ""), ("ому", ""), ("ого", ""), ("ой", "а"), ("у", ""), ("ом", ""), ("е", ""), ("а", "")):
        if w.endswith(suf) and re.search(r"(ов|ев|ин|ын)" + suf + "$", w):
            return w[: -len(suf)] + rep
    return w


@functools.lru_cache(maxsize=20000)
def name_gender(name: str) -> str:
    if re.search(r"(ов|ев|ин|ын|ий|ой)$", name):
        return "male"
    if _morph is not None:
        for p in _morph.parse(name)[:3]:
            if NAME_TAGS & set(p.tag.grammemes) and "nomn" in p.tag:
                if "masc" in p.tag:
                    return "male"
                if "femn" in p.tag:
                    return "female"
    if re.search(r"(ова|ева|ина|ына|ская|цкая|а|я)$", name):
        return "female"
    return "male"


_ENDINGS = ("ыми", "ими", "ому", "ему", "ой", "ей", "ым", "им", "ом", "ем", "ою", "ею", "ую", "у", "ю", "а", "я", "е", "ы", "и")


def name_key(word: str) -> str:
    """Грубая основа имени для склейки падежных форм: Хрюкину/Хрюкиным/Хрюкин → Хрюкин."""
    for e in _ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= 4:
            return word[: -len(e)]
    return word


def pick_canonical(forms: dict[str, int]) -> str:
    """Из падежных форм выбирает именительный: голая основа (Иван, Хрюкин) или основа+а/я (Наташа)."""
    keys = {name_key(f) for f in forms}
    for f in sorted(forms, key=lambda x: -forms[x]):
        if f in keys:
            return f
    for f in sorted(forms, key=lambda x: -forms[x]):
        if f.endswith(("а", "я")):
            return f
    return max(forms, key=lambda x: forms[x])
