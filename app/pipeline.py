"""Полный конвейер: источник → анализ → предпросмотр → TTS по ролям → музыка + ducking → LUFS → экспорт."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable

from app import config
from app.audio import export, mixer
from app.models import Book
from app.sources.normalize import count_words, text_hash
from app.sources.resolver import resolve
from app.text.analyzer import analyze
from app.tts.engines import engine_voice_ok, prosody_for, synthesize
from app.tts.voices import BASE_VOICES, all_voices, assign_voices, get_voice

log = logging.getLogger(__name__)
BOOKS = config.DATA_DIR / "books"
BOOKS.mkdir(parents=True, exist_ok=True)

Progress = Callable[[float, str], None]


def _noop(p: float, stage: str) -> None:
    pass


# ---------- этап 1-2: книга и анализ (кэшируется по хэшу текста) ----------

def prepare_book(params: dict, progress: Progress = _noop) -> dict:
    progress(0.02, "Поиск и загрузка текста")
    data = None
    if params.get("upload_path"):
        data = Path(params["upload_path"]).read_bytes()
    book = resolve(query=params.get("query", ""), title=params.get("title", ""), author=params.get("author", ""),
                   url=params.get("url", ""), isbn=params.get("isbn", ""),
                   filename=params.get("filename", ""), data=data)
    book_id = text_hash(book.full_text)[:16]
    bdir = BOOKS / book_id
    bdir.mkdir(exist_ok=True)
    (bdir / "book.json").write_text(json.dumps(book.to_dict(), ensure_ascii=False))
    ap = bdir / "analysis.json"
    use_llm = params.get("use_llm")
    if ap.exists() and not params.get("reanalyze"):
        analysis = json.loads(ap.read_text())
    else:
        progress(0.08, "Анализ текста: персонажи, реплики, эмоции")
        analysis = analyze(book, use_llm=use_llm, progress=lambda f: progress(0.08 + 0.12 * f, "LLM-разметка"))
        analysis["voices"] = assign_voices(analysis["characters"])
        ap.write_text(json.dumps(analysis, ensure_ascii=False))
    return {"book_id": book_id, "book": book, "analysis": analysis}


def book_summary(book_id: str) -> dict:
    bdir = BOOKS / book_id
    book = Book.from_dict(json.loads((bdir / "book.json").read_text()))
    analysis = json.loads((bdir / "analysis.json").read_text())
    first = next((s["text"] for s in analysis["chapters"][0]["segments"]), "") if analysis["chapters"] else ""
    return {
        "book_id": book_id, "title": book.title, "author": book.author, "language": book.language,
        "source": book.source, "source_url": book.source_url, "license_note": book.license_note,
        "cover_url": book.cover_url, "warnings": book.warnings,
        "words": count_words(book.full_text), "chapters": [
            {"index": i, "title": c.title, "words": count_words(c.text)} for i, c in enumerate(book.chapters)],
        "first_paragraph": first, "analysis_method": analysis.get("method"),
        "characters": analysis["characters"], "voices": analysis.get("voices", {}),
        "estimated_minutes": round(count_words(book.full_text) / 140, 1),
    }


def update_analysis(book_id: str, *, speakers: dict | None = None, emotions: dict | None = None,
                    voices: dict | None = None) -> dict:
    """Ручная правка: speakers/emotions — {"<глава>:<сегмент>": значение}, voices — {персонаж: voice_id}."""
    ap = BOOKS / book_id / "analysis.json"
    a = json.loads(ap.read_text())
    for key, val in (speakers or {}).items():
        ci, si = map(int, key.split(":"))
        seg = a["chapters"][ci]["segments"][si]
        seg["speaker"], seg["attributed_by"] = val, "manual"
        if val not in {c["name"] for c in a["characters"]} and val != "narrator":
            a["characters"].append({"name": val, "gender": "male", "age": "adult", "traits": "", "aliases": [], "lines": 0})
    for key, val in (emotions or {}).items():
        ci, si = map(int, key.split(":"))
        a["chapters"][ci]["segments"][si]["emotion"] = val
    counts: dict[str, int] = {}
    for ch in a["chapters"]:
        for s in ch["segments"]:
            if s["kind"] == "dialogue":
                counts[s["speaker"]] = counts.get(s["speaker"], 0) + 1
    for c in a["characters"]:
        c["lines"] = counts.get(c["name"], 0)
    a["characters"] = [c for c in a["characters"] if c["lines"] > 0]
    a["characters"].sort(key=lambda c: -c["lines"])
    cur = a.get("voices", {})
    cur.update(voices or {})
    a["voices"] = assign_voices(a["characters"], narrator=cur.get("narrator"),
                                overrides={k: v for k, v in cur.items() if k != "narrator"})
    ap.write_text(json.dumps(a, ensure_ascii=False))
    return a


# ---------- этап 3-6: озвучка и сборка ----------

PAUSE_PARAGRAPH = 0.6
PAUSE_INLINE = 0.22
PAUSE_AFTER_TITLE = 1.2


def _clips_for_chapter(ch: dict, voices: dict, settings: dict, limit_sec: float | None,
                       progress: Callable[[float], None], warn: list[str]) -> list[mixer.Clip]:
    segs = ch["segments"]
    clips: list[mixer.Clip] = []
    total_dur = 0.0
    narrator = get_voice(voices.get("narrator") or "edge-dmitry")
    rate_mul = float(settings.get("rate", 1.0))
    pitch_add = float(settings.get("pitch", 0.0))
    if settings.get("read_titles", True) and ch.get("title") and not limit_sec:
        p = synthesize(ch["title"] + ".", narrator, "neutral", 0.2, rate_mul, pitch_add)
        clips.append(mixer.Clip(p, PAUSE_AFTER_TITLE, 0.0, -1))
    for i, s in enumerate(segs):
        vid = voices.get(s["speaker"]) if s["kind"] == "dialogue" else voices.get("narrator")
        try:
            voice = get_voice(vid) if vid else narrator
        except KeyError:
            voice = narrator
        p = synthesize(s["text"], voice, s.get("emotion", "neutral"), s.get("intensity", 0.3), rate_mul, pitch_add)
        if p.name.endswith(".fb.wav") and voice.engine != "silero":
            msg = f"Голос {voice.title}: онлайн-движок недоступен, часть фраз озвучена резервным голосом"
            if msg not in warn:
                warn.append(msg)
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        pause = PAUSE_INLINE if nxt and nxt["paragraph"] == s["paragraph"] else PAUSE_PARAGRAPH
        _, _, vol = prosody_for(s.get("emotion", "neutral"), s.get("intensity", 0.3))
        clips.append(mixer.Clip(p, pause, vol if s["kind"] == "dialogue" else 0.0, i))
        import soundfile as sf
        total_dur += sf.info(str(p)).duration + pause
        progress((i + 1) / len(segs))
        if limit_sec and total_dur >= limit_sec:
            break
    return clips


NARRATOR_CHAIN = ["edge-svetlana", "edge-dmitry", "silero-eugene", "silero-baya"]


def _ensure_voices(voices: dict) -> list[str]:
    """Заранее проверяет онлайн-голоса; недоступные заменяет на всю книгу целиком (консистентность)."""
    warn = []
    used = set(voices.values())
    for role, vid in list(voices.items()):
        try:
            v = get_voice(vid)
        except KeyError:
            v = get_voice("silero-eugene")
        if engine_voice_ok(v):
            voices[role] = v.id
            continue
        if role == "narrator":
            chain = [x for x in NARRATOR_CHAIN if x != vid]
        else:
            chain = [x.id for x in BASE_VOICES if x.engine == "silero" and x.gender == v.gender and x.id not in used]
            chain += [x.id for x in BASE_VOICES if x.engine == "silero" and x.gender == v.gender]
        for alt in chain:
            try:
                av = get_voice(alt)
            except KeyError:
                continue
            if av.id == alt and engine_voice_ok(av):
                voices[role] = alt
                used.add(alt)
                who = "рассказчика" if role == "narrator" else f"персонажа «{role}»"
                warn.append(f"Голос {v.title} сейчас недоступен — для {who} на всю книгу выбран «{av.title}»")
                break
    return warn


def _scenes_to_times(scenes: list[dict], starts: dict[int, float], total: float) -> list[tuple[float, float, str]]:
    out = []
    known = sorted(starts.items())
    for k, sc in enumerate(scenes):
        idx = sc["start"]
        t = next((st for si, st in known if si >= idx), None)
        if t is None:
            break
        if k == 0:
            t = 0.0
        out.append([t, None, sc["mood"]])
    for k in range(len(out)):
        out[k][1] = out[k + 1][0] if k + 1 < len(out) else total
    # слишком короткие сцены (< 12 с) сливаем с предыдущей — без «дёрганой» музыки
    merged: list[list] = []
    for s, e, m in out:
        if merged and (e - s < 12 or merged[-1][2] == m):
            merged[-1][1] = e
        else:
            merged.append([s, e, m])
    return [tuple(x) for x in merged]


def render(job_dir: Path, book_id: str, settings: dict, progress: Progress = _noop) -> dict:
    t0 = time.time()
    bdir = BOOKS / book_id
    book = Book.from_dict(json.loads((bdir / "book.json").read_text()))
    analysis = json.loads((bdir / "analysis.json").read_text())
    voices = dict(analysis.get("voices") or assign_voices(analysis["characters"]))
    voices.update(settings.get("voices") or {})
    warn_pre = _ensure_voices(voices)
    chapters_idx = settings.get("chapters") or list(range(len(analysis["chapters"])))
    preview = bool(settings.get("preview"))
    if preview:
        chapters_idx = chapters_idx[:1]
    music_on = settings.get("music", True)
    formats = settings.get("formats") or ["mp3", "m4b"]
    work = job_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    warn: list[str] = list(book.warnings) + warn_pre
    results: dict = {"chapters": [], "files": {}, "voices": voices, "warnings": warn}
    n = len(chapters_idx)
    ch_wavs: list[Path] = []
    ch_titles: list[str] = []
    for k, ci in enumerate(chapters_idx):
        ch = analysis["chapters"][ci]
        base = 0.2 + 0.7 * k / n
        span = 0.7 / n
        progress(base, f"Озвучка: {ch['title']}")
        clips = _clips_for_chapter(ch, voices, settings, 30.0 if preview else None,
                                   lambda f: progress(base + span * 0.75 * f, f"Озвучка: {ch['title']}"), warn)
        progress(base + span * 0.8, f"Сведение: {ch['title']}")
        vpath = work / f"ch{ci:03d}_voice.wav"
        total, starts = mixer.build_voice_track(clips, vpath)
        mix = work / f"ch{ci:03d}_mix.wav"
        chap_info = {"index": ci, "title": ch["title"], "duration": round(total, 2)}
        if music_on:
            scenes = _scenes_to_times(ch.get("scenes") or [{"start": 0, "mood": "calm"}], starts, total)
            bed = work / f"ch{ci:03d}_music.wav"
            chap_info["music"] = mixer.build_music_bed(scenes, total, bed)
            ducked = work / f"ch{ci:03d}_music_ducked.wav"
            mixer.duck_and_mix(vpath, bed, mix, ducked, settings.get("music_gain_db"))
            chap_info["ducking"] = mixer.ducking_report(vpath, ducked)
        else:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(vpath), "-ac", "2", str(mix)], check=True)
        norm = work / f"ch{ci:03d}_norm.wav"
        export.loudnorm(mix, norm)
        chap_info["loudness"] = export.measure_lufs(norm)
        ch_wavs.append(norm)
        ch_titles.append(ch["title"])
        safe = f"{k + 1:02d}"
        if "mp3" in formats:
            out = job_dir / f"chapter_{safe}.mp3"
            export.to_mp3(norm, out, ch["title"], book.author, book.title, str(k + 1))
            chap_info["mp3"] = out.name
        results["chapters"].append(chap_info)

    progress(0.93, "Экспорт")
    full = work / "full.wav"
    starts = export.concat_wavs(ch_wavs, full)
    durs = [c["duration"] for c in results["chapters"]]
    if "mp3" in formats:
        out = job_dir / "book.mp3"
        if len(ch_wavs) == 1:
            shutil.copy(job_dir / "chapter_01.mp3", out)
        else:
            export.to_mp3(full, out, book.title, book.author, book.title)
        results["files"]["mp3"] = out.name
    if "wav" in formats:
        shutil.copy(full, job_dir / "book.wav")
        results["files"]["wav"] = "book.wav"
    if "m4b" in formats and not preview:
        out = job_dir / "book.m4b"
        chaps = [(t, s, s + d) for t, s, d in zip(ch_titles, starts, durs)]
        export.to_m4b(full, out, chaps, book.title, book.author)
        results["files"]["m4b"] = out.name
    results["loudness"] = export.measure_lufs(full)
    results["elapsed_sec"] = round(time.time() - t0, 1)
    results["duration_sec"] = round(sum(durs), 1)
    if not settings.get("keep_work"):
        for p in work.glob("*_music.wav"):
            p.unlink()
    progress(1.0, "Готово")
    return results


def list_voices() -> list[dict]:
    return [v.to_dict() for v in all_voices()]
