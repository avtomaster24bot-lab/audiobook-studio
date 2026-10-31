"""NLP/LLM-слой: персонажи, атрибуция реплик, эмоции, сцены. LLM → при отсутствии ключа rule-based."""
from __future__ import annotations

import logging
from dataclasses import asdict

from app.models import Book
from app.text import llm
from app.text.attribution import Character, attribute, merge_false_remarks
from app.text.emotions import MOOD_OF, scene_moods, tag_segment
from app.text.structure import Segment, segment_chapter

log = logging.getLogger(__name__)
LLM_BATCH = 80


def analyze(book: Book, use_llm: bool | None = None, progress=None) -> dict:
    chapters_segs = [merge_false_remarks(segment_chapter(ch.text)) for ch in book.chapters]
    chars, chapters_segs = attribute(chapters_segs)
    for segs in chapters_segs:
        for s in segs:
            tag_segment(s)
    scenes = [scene_moods(segs) for segs in chapters_segs]
    method = "rules"

    use_llm = llm.enabled() if use_llm is None else (use_llm and llm.enabled())
    if use_llm:
        try:
            chars, scenes = _llm_refine(chapters_segs, chars, scenes, progress)
            method = "llm"
        except Exception as e:  # LLM — улучшение, а не точка отказа
            log.exception("LLM analysis failed, using rules: %s", e)

    return {
        "method": method,
        "characters": [c.to_dict() for c in chars],
        "chapters": [
            {"title": ch.title, "segments": [asdict(s) for s in segs], "scenes": sc}
            for ch, segs, sc in zip(book.chapters, chapters_segs, scenes)
        ],
    }


def _llm_refine(chapters_segs: list[list[Segment]], chars: list[Character], scenes: list, progress=None):
    known: dict[str, dict] = {c.name: c.to_dict() for c in chars}
    new_scenes = []
    total = sum(len(s) for s in chapters_segs)
    done = 0
    for ci, segs in enumerate(chapters_segs):
        ch_scenes: list[dict] = []
        for b in range(0, len(segs), LLM_BATCH):
            batch = segs[b:b + LLM_BATCH]
            numbered = [{"i": b + j, "kind": s.kind, "text": s.text} for j, s in enumerate(batch)]
            res = llm.analyze_segments(numbered, list(known.values()))
            if not res:
                raise RuntimeError("empty LLM response")
            for c in res.get("characters", []):
                if c.get("name") and c["name"] != "narrator":
                    prev = known.get(c["name"], {})
                    prev.update({k: v for k, v in c.items() if v})
                    prev.setdefault("lines", 0)
                    known[c["name"]] = prev
            for item in res.get("segments", []):
                i = item.get("i")
                if not isinstance(i, int) or not (0 <= i < len(segs)):
                    continue
                s = segs[i]
                if s.kind == "dialogue" and item.get("speaker") and item["speaker"] != "narrator":
                    s.speaker, s.attributed_by = item["speaker"], "llm"
                if item.get("emotion") in llm.EMOTIONS:
                    s.emotion = item["emotion"]
                if isinstance(item.get("intensity"), (int, float)):
                    s.intensity = max(0.0, min(1.0, float(item["intensity"])))
            for sc in res.get("scenes", []):
                if isinstance(sc.get("start"), int) and sc.get("mood") in llm.MOODS:
                    ch_scenes.append({"start": sc["start"], "mood": sc["mood"]})
            done += len(batch)
            if progress:
                progress(done / total)
        if not ch_scenes:
            ch_scenes = scenes[ci]
        ch_scenes.sort(key=lambda x: x["start"])
        ch_scenes[0]["start"] = 0
        new_scenes.append(ch_scenes)
    counts: dict[str, int] = {}
    for segs in chapters_segs:
        for s in segs:
            if s.kind == "dialogue":
                counts[s.speaker] = counts.get(s.speaker, 0) + 1
    out = []
    for name, n in sorted(counts.items(), key=lambda x: -x[1]):
        d = known.get(name, {"name": name})
        out.append(Character(name=name, gender=d.get("gender", "unknown"), age=d.get("age", "adult"),
                             traits=d.get("traits", ""), aliases=set(d.get("aliases", []) or []), lines=n))
    return out, new_scenes


__all__ = ["analyze", "MOOD_OF"]
