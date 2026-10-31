"""Нормализация текста перед озвучкой: кодировка, кавычки, тире, пробелы, переносы."""
from __future__ import annotations

import hashlib
import re
import unicodedata

try:
    import ftfy
except ImportError:  # pragma: no cover
    ftfy = None

DASHES = "\u2012\u2013\u2014\u2015\u2212"


def decode_bytes(raw: bytes) -> str:
    """Определяет кодировку (utf-8 / cp1251 / koi8-r) и чинит mojibake."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return fix_mojibake(raw.decode(enc, errors="replace").lstrip("\ufeff"))
    try:
        return fix_mojibake(raw.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    best, best_score = None, -1.0
    for enc in ("cp1251", "koi8-r", "cp866", "latin-1"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = _cyr_score(text)
        if score > best_score:
            best, best_score = text, score
    return fix_mojibake(best or raw.decode("utf-8", errors="replace"))


def _cyr_score(text: str) -> float:
    sample = text[:20000]
    if not sample:
        return 0.0
    common = sum(sample.count(c) for c in "оеаинтсрвл ОЕАИНТСРВЛ")
    return common / len(sample)


def fix_mojibake(text: str) -> str:
    if ftfy is not None:
        text = ftfy.fix_text(text, normalization="NFC", uncurl_quotes=False)
    # типичный «РџСЂРёРІРµС‚» (utf-8, прочитанный как cp1251)
    if re.search(r"(?:Р[ЃЂ-Яа-я]|С[ЂЃ‚ѓ„…†‡€‰Љ‹ЊЌЋЏ]){4,}", text):
        try:
            text = text.encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def normalize_text(text: str) -> str:
    """Типографская нормализация для TTS.

    * NFC, неразрывные пробелы → обычные;
    * все виды тире → «—», дефисы-тире в начале реплики → «—»;
    * кавычки „“ “” " → «»;
    * склейка переносов «сло-\nво»;
    * схлопывание пробелов, пустых строк.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u00a0\u2007\u202f\u2009\u200a\u2002\u2003]", " ", text)
    text = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]", "", text)
    text = re.sub(f"[{DASHES}]", "—", text)
    # дефис в роли тире: в начале строки или между пробелами
    text = re.sub(r"(?m)^(\s*)-{1,2}\s+", r"\1— ", text)
    text = re.sub(r"\s-{1,2}\s", " — ", text)
    # кавычки
    text = text.replace("„", "«").replace("“", "»") if "„" in text else text
    text = re.sub(r"[\u201c\u201f]", "«", text)
    text = re.sub(r"[\u201d]", "»", text)
    text = _straight_quotes(text)
    text = text.replace("...", "…")
    # склейка переносов
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _straight_quotes(text: str) -> str:
    out, open_ = [], True
    for ch in text:
        if ch == '"':
            out.append("«" if open_ else "»")
            open_ = not open_
        else:
            out.append(ch)
    return "".join(out)


def text_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def count_words(text: str) -> int:
    return len(re.findall(r"\w+", text))
