"""Rule-based извлечение персонажей и атрибуция реплик для русской прозы.

Работает без LLM: «— сказал Иван», «Иван спросил», «— говорит городовой», обращения
по имени («Эй, Прохор!» → отвечает Прохор), чередование собеседников, роли-синонимы
(«надзиратель Очумелов» → «надзиратель» = Очумелов).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.text.morph import is_person_name, name_gender, name_key, pick_canonical
from app.text.structure import Segment

try:
    from app.text.morph import _morph
except Exception:  # pragma: no cover
    _morph = None

SPEECH_VERBS = (
    "сказа", "говор", "спрос", "спраш", "ответ", "отвеча", "крикн", "крич", "закрич", "шепн", "шепч", "шепт",
    "прошепт", "воскликн", "восклица", "пробормот", "бормоч", "проговор", "продолжа", "продолжи", "замеча",
    "замети", "возраз", "перебив", "перебил", "произн", "подума", "дума", "буркн", "рявкн", "отозва",
    "обраща", "обрати", "вздохн", "засмея", "смеёт", "смеет", "хохоч", "грози", "пригрози", "начина", "начал",
    "добав", "повтор", "сообщ", "объясн", "предлож", "попрос", "прос", "вскрикн", "взвизгн", "простона",
    "стона", "огрызн", "усмехн", "улыбн", "кивн", "позва", "зов", "прерва", "подхват", "согласи", "заяв",
    "приказ", "велел", "провозгласи", "умоля", "всхлипн", "плач", "заплака", "вмешал", "пропищ", "прохрип",
    "процеди", "пропе", "напомни", "окликн", "поясни", "решил", "уточни", "забормота", "кашля",
)
PERCEPTION_VERBS = ("слыш", "послыша", "раздал", "раздаёт", "раздает", "донёс", "донос")
FEMALE_PAST = re.compile(r"\b\w+(ла|лась)\b")
ROLE_NOUNS = {
    "городовой": "male", "надзиратель": "male", "повар": "male", "генерал": "male", "доктор": "male",
    "офицер": "male", "солдат": "male", "барин": "male", "барыня": "female", "лакей": "male", "кучер": "male",
    "хозяин": "male", "хозяйка": "female", "старик": "male", "старуха": "female", "мужик": "male", "баба": "female",
    "девушка": "female", "девочка": "female", "мальчик": "male", "дама": "female", "господин": "male",
    "незнакомец": "male", "незнакомка": "female", "мать": "female", "отец": "male", "жена": "female",
    "муж": "male", "сын": "male", "дочь": "female", "князь": "male", "княгиня": "female", "граф": "male",
    "графиня": "female", "купец": "male", "чиновник": "male", "студент": "male", "учитель": "male",
    "священник": "male", "батюшка": "male", "няня": "female", "горничная": "female", "дьякон": "male",
    "смотритель": "male", "ямщик": "male", "сторож": "male", "приказчик": "male", "капитан": "male",
}
CROWD = re.compile(r"(кто-то|голос|кто-нибудь)(\s+из\s+(толпы|публики|зала|народа|угла))?", re.I)
GENERIC = {"Голос из толпы", "Неизвестный"}
PRONOUNS = {"он": "male", "она": "female"}
WORD = re.compile(r"[А-ЯЁа-яё][а-яё-]*")
STOP_CAPS = {
    "Ваше", "Вы", "Ты", "Он", "Она", "Они", "Мы", "Я", "Это", "Так", "Да", "Нет", "Ну", "Ах", "Ох", "Эй", "Гм",
    "Бог", "Господи", "Господь", "Москва", "Москве", "Петербург", "Петербурге", "Россия", "России", "Ребята",
}


@dataclass
class Character:
    name: str
    gender: str = "unknown"
    age: str = "adult"
    traits: str = ""
    aliases: set[str] = field(default_factory=set)
    lines: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "gender": self.gender, "age": self.age, "traits": self.traits,
                "aliases": sorted(self.aliases), "lines": self.lines}


def _lemma(word: str) -> str:
    if _morph is None:
        return word.lower()
    return _morph.parse(word.lower())[0].normal_form


def role_of(word: str) -> str | None:
    """«городовому» → «городовой», если слово — роль-нарицательное."""
    w = word.lower()
    if w in ROLE_NOUNS:
        return w
    if _morph is not None:
        for p in _morph.parse(w):
            if p.normal_form in ROLE_NOUNS:
                return p.normal_form
            inf = p.inflect({"nomn", "sing"})
            if inf and inf.word in ROLE_NOUNS:
                return inf.word
    return None


def _is_verb(word: str) -> bool:
    w = word.lower()
    if w.startswith(SPEECH_VERBS):
        return True
    if _morph is not None:
        p = _morph.parse(w)[0]
        return p.tag.POS in ("VERB", "GRND") and p.score > 0.3
    return False


def is_remark(text: str) -> bool:
    """Похож ли кусок между тире на авторскую ремарку («сказал Иван», «Иван спросил»)."""
    words = WORD.findall(text)[:4]
    if not words:
        return False
    low = [w.lower() for w in words]
    if any(w.startswith(SPEECH_VERBS) or w.startswith(PERCEPTION_VERBS) for w in low[:3]):
        return True
    # «Иван, помолчав, сказал» / «глубокомысленно замечает»
    return len(words) >= 2 and _is_verb(words[1]) and (is_person_name(words[0]) or words[0].endswith("о"))


def merge_false_remarks(segs: list[Segment]) -> list[Segment]:
    """Склеивает обратно куски реплики, ошибочно отделённые как ремарка («…Митричем, — и вдруг…»)."""
    out: list[Segment] = []
    for s in segs:
        if s.kind == "narration" and s.attribution_hint == "" and out and out[-1].kind == "dialogue" \
                and out[-1].paragraph == s.paragraph and not is_remark(s.text):
            out[-1].text = out[-1].text.rstrip() + " — " + s.text
            out[-1].extra["merged"] = True
            continue
        if s.kind == "dialogue" and out and out[-1].kind == "dialogue" and out[-1].paragraph == s.paragraph \
                and out[-1].extra.get("merged"):
            out[-1].text = out[-1].text.rstrip() + " " + s.text
            continue
        out.append(s)
    # пересчитываем подсказки-ремарки для реплик абзаца
    by_par: dict[int, list[Segment]] = defaultdict(list)
    for s in out:
        by_par[s.paragraph].append(s)
    for par in by_par.values():
        hints = " ".join(x.text for x in par if x.kind == "narration")
        if any(x.kind == "dialogue" for x in par) and len(par) > 1:
            for x in par:
                if x.kind == "dialogue":
                    x.attribution_hint = hints
    return out


class CharacterIndex:
    def __init__(self) -> None:
        self.chars: dict[str, Character] = {}
        self.key2name: dict[str, str] = {}
        self.role2name: dict[str, str] = {}

    def add(self, name: str, gender: str = "unknown") -> Character:
        if name in self.chars:
            return self.chars[name]
        c = Character(name=name, gender=gender if gender != "unknown" else name_gender(name))
        self.chars[name] = c
        self.key2name[name_key(name)] = name
        return c

    def lookup(self, word: str) -> str | None:
        if word in self.chars:
            return word
        k = name_key(word)
        if k in self.key2name:
            return self.key2name[k]
        lem = role_of(word) or _lemma(word)
        if lem in self.role2name:
            return self.role2name[lem]
        if lem in self.chars:
            return lem
        return None


def collect_names(texts: list[str]) -> dict[str, dict[str, int]]:
    """Имена собственные (не в начале предложения) → группы падежных форм."""
    groups: dict[str, Counter] = defaultdict(Counter)
    for t in texts:
        for m in re.finditer(r"(?<![.!?…«—]\s)(?<!^)\b([А-ЯЁ][а-яё]{2,})\b", t):
            w = m.group(1)
            if w in STOP_CAPS:
                continue
            if is_person_name(w):
                groups[name_key(w)][w] += 1
        # «— Иван, …», «Эй, Прохор!» в начале тоже ловим, если это явно имя
        for m in re.finditer(r"(?:^|[,!?]\s)([А-ЯЁ][а-яё]{2,})(?=[,!?])", t):
            w = m.group(1)
            if w not in STOP_CAPS and is_person_name(w):
                groups[name_key(w)][w] += 1
    return groups


def find_speaker_in_remark(remark: str, idx: CharacterIndex) -> tuple[str | None, bool]:
    """Возвращает (speaker, perception) — perception=True, если ремарка вида «слышит Иван» (Иван НЕ говорит)."""
    words = WORD.findall(remark)
    low = [w.lower() for w in words]
    perception = any(w.startswith(PERCEPTION_VERBS) for w in low[:3])
    if CROWD.search(remark) and (low[:4] and any(w in ("кто-то", "голос", "кто-нибудь") for w in low[:4])):
        return "Голос из толпы", perception
    verb_i = next((i for i, w in enumerate(low[:4]) if w.startswith(SPEECH_VERBS) or w.startswith(PERCEPTION_VERBS) or _is_verb(w)), None)
    order = []
    if verb_i is not None:
        order = list(range(verb_i + 1, min(len(words), verb_i + 4))) + list(range(verb_i - 1, -1, -1))
    else:
        order = list(range(min(len(words), 4)))
    for i in order:
        w = words[i]
        # «к городовому» — адресат, не говорящий
        if i > 0 and low[i - 1] in ("к", "ко", "на", "у"):
            continue
        name = idx.lookup(w)
        if name:
            return name, perception
        role = role_of(w)
        if role:
            return role, perception
        if low[i] in PRONOUNS:
            return "@" + low[i], perception
    return None, perception


VOC_RE = re.compile(r"(?:^|[,!?….]\s|\b(?:эй|брат|милый|голубчик)\s)([А-ЯЁ][а-яё]{2,})(?=\s*[,!?…])")


def vocatives(text: str, idx: CharacterIndex) -> list[str]:
    out = []
    for m in VOC_RE.finditer(text):
        n = idx.lookup(m.group(1))
        if n:
            out.append(n)
    return out


def addressee_in_remark(remark: str) -> str | None:
    m = re.search(r"\bк\s+([А-ЯЁа-яё]+)", remark)
    if m:
        w = m.group(1)
        return role_of(w) or (w if w[0].isupper() else _lemma(w))
    return None


def attribute(chapters_segments: list[list[Segment]]) -> tuple[list[Character], list[list[Segment]]]:
    """Атрибуция по всей книге (персонажи общие для всех глав)."""
    all_segs = [s for ch in chapters_segments for s in ch]
    idx = CharacterIndex()
    groups = collect_names([s.text for s in all_segs])
    for key, forms in groups.items():
        total = sum(forms.values())
        if total >= 1:
            idx.add(pick_canonical(dict(forms)))

    # роли-синонимы: «надзиратель Очумелов», «городовой Елдырин»
    for s in all_segs:
        for m in re.finditer(r"\b([а-яё]+)\s+([А-ЯЁ][а-яё]+)", s.text):
            lem = role_of(m.group(1))
            name = idx.lookup(m.group(2))
            if lem in ROLE_NOUNS and name and lem not in idx.role2name:
                idx.role2name[lem] = name
                idx.chars[name].aliases.add(lem)
    # обращение по имени + «обращается … к городовому» → городовой = это имя
    for s in all_segs:
        if s.kind == "dialogue" and s.attribution_hint:
            addr = addressee_in_remark(s.attribution_hint)
            voc = vocatives(s.text, idx)
            if addr in ROLE_NOUNS and len(set(voc)) == 1 and addr not in idx.role2name:
                idx.role2name[addr] = voc[0]
                idx.chars[voc[0]].aliases.add(addr)

    last_mentioned: dict[str, str] = {}
    for ch in chapters_segments:
        history: list[str] = []          # говорящие подряд в текущем диалоге
        addressed_next: str | None = None
        prev_par = -2
        for s in ch:
            if s.kind == "narration" and not s.attribution_hint:
                is_inline_remark = any(x.paragraph == s.paragraph and x.kind == "dialogue" for x in ch)
                if not is_inline_remark:
                    for w in WORD.findall(s.text):
                        n = idx.lookup(w)
                        if n:
                            last_mentioned[idx.chars[n].gender if n in idx.chars else "male"] = n
                    if len(s.text) > 200:
                        history = []
                continue
            if s.kind != "dialogue":
                continue
            speaker, how = None, ""
            excluded: set[str] = set(vocatives(s.text, idx))
            if s.attribution_hint:
                sp, perception = find_speaker_in_remark(s.attribution_hint, idx)
                if sp and perception and not sp.startswith("@"):
                    excluded.add(idx.lookup(sp) or sp)
                elif sp:
                    if sp.startswith("@"):
                        g = PRONOUNS[sp[1:]]
                        sp = last_mentioned.get(g)
                    if sp:
                        speaker, how = (idx.role2name.get(sp, sp)), "rule:remark"
            if not speaker and addressed_next and addressed_next not in excluded:
                speaker, how = addressed_next, "rule:vocative-reply"
            if not speaker and len(history) >= 2 and s.paragraph != prev_par:
                cand = next((h for h in reversed(history[:-1])
                             if h not in GENERIC and h != history[-1] and h not in excluded), None)
                if cand:
                    speaker, how = cand, "turn-taking"
            if not speaker and history and s.paragraph == prev_par:
                speaker, how = history[-1], "same-paragraph"
            if not speaker:
                speaker, how = "Неизвестный", "unknown"
            s.speaker, s.attributed_by = speaker, how
            if speaker not in idx.chars:
                g = ROLE_NOUNS.get(speaker, "male")
                if speaker == "Голос из толпы":
                    g = "male"
                if FEMALE_PAST.search(s.attribution_hint or "") and speaker in ROLE_NOUNS:
                    g = "female"
                idx.add(speaker, g)
            if not history or history[-1] != speaker:
                history.append(speaker)
            v = vocatives(s.text, idx)
            addressed_next = v[-1] if v and v[-1] != speaker else None
            if s.attribution_hint:
                addr = addressee_in_remark(s.attribution_hint)
                if addr:
                    who = idx.lookup(addr) or idx.role2name.get(addr)
                    if who and who != speaker:
                        addressed_next = who
            prev_par = s.paragraph

    for s in all_segs:
        if s.kind == "dialogue" and s.speaker in idx.chars:
            idx.chars[s.speaker].lines += 1
    chars = sorted((c for c in idx.chars.values() if c.lines > 0), key=lambda c: -c.lines)
    for c in chars:
        c.age = "old" if c.name in ("старик", "старуха", "генерал") else c.age
        c.name = c.name[:1].upper() + c.name[1:]
    for s in all_segs:
        if s.kind == "dialogue":
            s.speaker = s.speaker[:1].upper() + s.speaker[1:]
    return chars, chapters_segments
