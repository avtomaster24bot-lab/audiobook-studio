"""REST API + веб-интерфейс сервиса «Аудиокнига-студия».

Запуск: uvicorn app.main:app --host 0.0.0.0 --port 8000
Документация API: /docs (Swagger), /redoc.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, pipeline
from app.audio.music import library as music_library
from app.jobs import JOBS, JobStore, get_queue
from app.models import SourceError
from app.tts.engines import synthesize
from app.tts.voices import get_voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(
    title="Аудиокнига-студия API",
    description="Книга → многоголосый аудиоспектакль: персонажи, эмоции, музыка с ducking, −16 LUFS, MP3/M4B.",
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOADS = config.DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)
METRICS = {"jobs_created": 0, "jobs_deduplicated": 0, "requests": 0, "started": time.time()}


@app.middleware("http")
async def count(request: Request, call_next):
    METRICS["requests"] += 1
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    get_queue()


def _err(e: SourceError, status: int = 422):
    return JSONResponse(status_code=status, content={"error": {"code": e.code, "message": e.message,
                                                               "suggestions": e.suggestions}})


# ---------------- Источники и предпросмотр ----------------

@app.post("/api/books/resolve", summary="Найти/загрузить книгу и разметить (без озвучки)")
async def resolve_book(
    query: str = Form(""), title: str = Form(""), author: str = Form(""), url: str = Form(""), isbn: str = Form(""),
    use_llm: Optional[bool] = Form(None), reanalyze: bool = Form(False), file: UploadFile | None = File(None),
):
    params = {"query": query, "title": title, "author": author, "url": url, "isbn": isbn, "use_llm": use_llm,
              "reanalyze": reanalyze}
    if file is not None and file.filename:
        dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
        dest.write_bytes(await file.read())
        params.update(upload_path=str(dest), filename=file.filename)
    try:
        import anyio

        prep = await anyio.to_thread.run_sync(lambda: pipeline.prepare_book(params))
    except SourceError as e:
        return _err(e)
    return pipeline.book_summary(prep["book_id"])


@app.get("/api/books/{book_id}", summary="Сводка по книге: метаданные, персонажи, голоса")
def get_book(book_id: str):
    try:
        return pipeline.book_summary(book_id)
    except FileNotFoundError:
        raise HTTPException(404, "Книга не найдена")


@app.get("/api/books/{book_id}/segments", summary="Разметка главы (для ручной правки)")
def get_segments(book_id: str, chapter: int = 0):
    p = pipeline.BOOKS / book_id / "analysis.json"
    if not p.exists():
        raise HTTPException(404)
    a = json.loads(p.read_text())
    if not 0 <= chapter < len(a["chapters"]):
        raise HTTPException(404, "Нет такой главы")
    ch = a["chapters"][chapter]
    return {"title": ch["title"], "scenes": ch["scenes"], "segments": [
        {"i": i, **{k: s[k] for k in ("kind", "text", "speaker", "emotion", "intensity", "attributed_by", "paragraph")}}
        for i, s in enumerate(ch["segments"])]}


class AnalysisEdit(BaseModel):
    speakers: dict[str, str] = Field(default_factory=dict, description='{"<глава>:<сегмент>": "Имя"}')
    emotions: dict[str, str] = Field(default_factory=dict)
    voices: dict[str, str] = Field(default_factory=dict, description='{"Имя персонажа" | "narrator": "voice_id"}')


@app.patch("/api/books/{book_id}/analysis", summary="Ручная правка атрибуции, эмоций, голосов")
def edit_analysis(book_id: str, edit: AnalysisEdit):
    if not (pipeline.BOOKS / book_id).exists():
        raise HTTPException(404)
    pipeline.update_analysis(book_id, speakers=edit.speakers, emotions=edit.emotions, voices=edit.voices)
    return pipeline.book_summary(book_id)


# ---------------- Задачи ----------------

class JobSettings(BaseModel):
    chapters: list[int] | None = None
    preview: bool = False
    voices: dict[str, str] = Field(default_factory=dict)
    rate: float = Field(1.0, ge=0.7, le=1.4)
    pitch: float = Field(0.0, ge=-4, le=4)
    music: bool = True
    music_gain_db: float | None = Field(None, ge=-30, le=0)
    formats: list[str] = Field(default_factory=lambda: ["mp3", "m4b"])
    read_titles: bool = True


class JobRequest(BaseModel):
    book_id: str | None = None
    query: str = ""
    title: str = ""
    author: str = ""
    url: str = ""
    isbn: str = ""
    use_llm: bool | None = None
    webhook_url: str | None = None
    settings: JobSettings = Field(default_factory=JobSettings)


def _public(job: dict) -> dict:
    j = {k: job.get(k) for k in ("id", "status", "progress", "stage", "eta_sec", "created_at", "started_at",
                                 "finished_at", "book_id", "book", "error", "deduplicated", "attempts")}
    if job.get("error"):
        j["error"] = {k: v for k, v in job["error"].items() if k != "trace"}
    res = job.get("result")
    if res:
        base = f"/api/jobs/{job['id']}/files/"
        j["result"] = {
            **{k: res.get(k) for k in ("duration_sec", "elapsed_sec", "loudness", "warnings", "voices")},
            "files": {k: base + v for k, v in res.get("files", {}).items()},
            "chapters": [{**c, "url": base + c["mp3"] if c.get("mp3") else None} for c in res.get("chapters", [])],
        }
    j["preview"] = bool((job.get("params") or {}).get("settings", {}).get("preview"))
    return j


@app.post("/api/jobs", status_code=202, summary="Создать задачу озвучки (JSON: book_id / query / url / isbn)")
def create_job(req: JobRequest):
    if not (req.book_id or req.query or req.title or req.url or req.isbn):
        raise HTTPException(400, "Нужен book_id, query, title, url или isbn")
    params = req.model_dump()
    params["settings"] = req.settings.model_dump()
    if req.book_id:
        ap = pipeline.BOOKS / req.book_id / "analysis.json"
        if not ap.exists():
            raise HTTPException(404, "Книга не найдена — сначала /api/books/resolve")
        params["analysis_version"] = int(ap.stat().st_mtime)  # правка разметки → новая задача
    job = get_queue().submit(params)
    METRICS["jobs_deduplicated" if job.get("deduplicated") else "jobs_created"] += 1
    return _public(job)


@app.post("/api/jobs/upload", status_code=202, summary="Создать задачу из файла (EPUB/FB2/TXT/DOCX/PDF)")
async def create_job_upload(file: UploadFile = File(...), settings: str = Form("{}"), webhook_url: str = Form(""),
                            use_llm: Optional[bool] = Form(None)):
    dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{Path(file.filename or 'book.txt').name}"
    data = await file.read()
    dest.write_bytes(data)
    import hashlib

    params = {"upload_path": str(dest), "filename": file.filename, "use_llm": use_llm,
              "file_sha256": hashlib.sha256(data).hexdigest(), "webhook_url": webhook_url or None,
              "settings": JobSettings(**json.loads(settings or "{}")).model_dump()}
    job = get_queue().submit(params)
    return _public(job)


@app.get("/api/jobs", summary="Библиотека: все задачи")
def list_jobs(limit: int = 50):
    return [_public(j) for j in JobStore.all()[:limit]]


@app.get("/api/jobs/{job_id}", summary="Статус задачи и прогресс")
def job_status(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return _public(job)


@app.get("/api/jobs/{job_id}/files/{name}", summary="Скачать результат (MP3 / M4B / WAV / главы)")
def job_file(job_id: str, name: str):
    if "/" in name or ".." in name:
        raise HTTPException(400)
    p = JOBS / job_id / name
    if not p.exists():
        raise HTTPException(404)
    media = {"mp3": "audio/mpeg", "m4b": "audio/mp4", "wav": "audio/wav"}.get(p.suffix[1:], "application/octet-stream")
    return FileResponse(p, media_type=media, filename=name)


@app.post("/api/jobs/{job_id}/regenerate", status_code=202, summary="Перегенерировать главы задачи")
def regenerate(job_id: str, chapters: list[int] | None = None):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(404)
    params = dict(job["params"])
    if job.get("book_id"):
        params["book_id"] = job["book_id"]
    params["settings"] = {**params.get("settings", {}), "chapters": chapters or params.get("settings", {}).get("chapters")}
    params["regen_nonce"] = uuid.uuid4().hex  # обходит дедупликацию осознанно
    return _public(get_queue().submit(params))


@app.delete("/api/jobs/{job_id}", summary="Удалить задачу и файлы (право на удаление, 152-ФЗ/GDPR)")
def delete_job(job_id: str):
    import shutil

    d = JOBS / job_id
    if not d.exists():
        raise HTTPException(404)
    job = JobStore.get(job_id) or {}
    up = (job.get("params") or {}).get("upload_path")
    if up:
        Path(up).unlink(missing_ok=True)
    shutil.rmtree(d)
    return {"deleted": job_id}


# ---------------- Голоса, музыка, служебное ----------------

@app.get("/api/voices", summary="Каталог голосов")
def voices():
    return pipeline.list_voices()


PREVIEW_TEXT = "— Это ты, Елдырин? Кто кричал? — спросил он. Кругом тишина… На площади ни души."


@app.get("/api/voices/{voice_id}/preview", summary="Превью голоса (MP3)")
def voice_preview(voice_id: str, text: str = PREVIEW_TEXT):
    try:
        v = get_voice(voice_id)
    except KeyError:
        raise HTTPException(404, "Нет такого голоса")
    text = text[:300]
    import hashlib
    import subprocess

    key = hashlib.sha1(f"{v.id}|{text}".encode()).hexdigest()[:16]
    out = config.DATA_DIR / "previews" / f"{v.id}-{key}.mp3"
    if not out.exists():
        wav = synthesize(text, v, "neutral", 0.3)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5",
                        "-c:a", "libmp3lame", "-b:a", "128k", str(out)], check=True)
    return FileResponse(out, media_type="audio/mpeg")


@app.get("/api/music", summary="Библиотека фоновой музыки")
def music():
    return music_library()


@app.get("/api/music/{mood}", summary="Прослушать трек настроения")
def music_track(mood: str):
    from app.audio.music import track_for

    p = track_for(mood)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg")


@app.get("/api/health")
def health():
    return {"status": "ok", "llm": config.LLM_ENABLED, "engines": config.TTS_ENGINES,
            "queue_size": get_queue().q.qsize()}


@app.get("/metrics", response_class=PlainTextResponse, summary="Метрики Prometheus")
def metrics():
    jobs = JobStore.all()
    by = {}
    for j in jobs:
        by[j["status"]] = by.get(j["status"], 0) + 1
    lines = [
        "# TYPE audiobook_requests_total counter", f"audiobook_requests_total {METRICS['requests']}",
        "# TYPE audiobook_jobs_created_total counter", f"audiobook_jobs_created_total {METRICS['jobs_created']}",
        "# TYPE audiobook_jobs_dedup_total counter", f"audiobook_jobs_dedup_total {METRICS['jobs_deduplicated']}",
        "# TYPE audiobook_queue_size gauge", f"audiobook_queue_size {get_queue().q.qsize()}",
        "# TYPE audiobook_uptime_seconds gauge", f"audiobook_uptime_seconds {time.time() - METRICS['started']:.0f}",
    ]
    for s, n in by.items():
        lines.append(f'audiobook_jobs{{status="{s}"}} {n}')
    return "\n".join(lines) + "\n"


app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
