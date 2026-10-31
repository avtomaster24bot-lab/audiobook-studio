"""Асинхронные задачи: in-process очередь + пул воркеров, статус и прогресс на диске, webhook, идемпотентность.

Для горизонтального масштабирования замените JobQueue на Celery/RQ (интерфейс submit/get остаётся тем же).
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path

import httpx

from app import config
from app.models import SourceError

log = logging.getLogger(__name__)
JOBS = config.DATA_DIR / "jobs"


def _now() -> float:
    return round(time.time(), 1)


class JobStore:
    _lock = threading.Lock()

    @staticmethod
    def path(job_id: str) -> Path:
        return JOBS / job_id / "job.json"

    @classmethod
    def save(cls, job: dict) -> None:
        p = cls.path(job["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with cls._lock:
            tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1))
            tmp.replace(p)

    @classmethod
    def get(cls, job_id: str) -> dict | None:
        p = cls.path(job_id)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    @classmethod
    def all(cls) -> list[dict]:
        out = []
        for p in JOBS.glob("*/job.json"):
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
        return sorted(out, key=lambda j: -j.get("created_at", 0))

    @classmethod
    def find_by_key(cls, key: str) -> dict | None:
        for j in cls.all():
            if j.get("idempotency_key") == key and j.get("status") in ("queued", "running", "done"):
                return j
        return None


def idempotency_key(params: dict) -> str:
    keep = {k: v for k, v in params.items() if k not in ("webhook_url", "upload_path")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


class JobQueue:
    def __init__(self, workers: int = 1) -> None:
        self.q: queue.Queue[str] = queue.Queue()
        self.threads = [threading.Thread(target=self._loop, daemon=True, name=f"worker-{i}") for i in range(workers)]
        for t in self.threads:
            t.start()
        # задачи, прерванные рестартом, ставим заново (идемпотентно: кэш TTS сохраняется)
        for j in JobStore.all():
            if j.get("status") in ("queued", "running"):
                j["status"] = "queued"
                JobStore.save(j)
                self.q.put(j["id"])

    def submit(self, params: dict, idem_key: str | None = None) -> dict:
        key = idem_key or idempotency_key(params)
        existing = JobStore.find_by_key(key)
        if existing:
            existing["deduplicated"] = True
            return existing
        job = {
            "id": uuid.uuid4().hex[:12], "status": "queued", "progress": 0.0, "stage": "В очереди",
            "params": params, "idempotency_key": key, "created_at": _now(), "updated_at": _now(),
            "attempts": 0, "eta_sec": None, "result": None, "error": None,
        }
        JobStore.save(job)
        self.q.put(job["id"])
        return job

    def _loop(self) -> None:
        while True:
            job_id = self.q.get()
            try:
                self._run(job_id)
            except Exception:  # pragma: no cover
                log.exception("worker crash")
            finally:
                self.q.task_done()

    def _run(self, job_id: str) -> None:
        from app import pipeline

        job = JobStore.get(job_id)
        if not job or job["status"] not in ("queued",):
            return
        job.update(status="running", started_at=_now(), attempts=job.get("attempts", 0) + 1)
        JobStore.save(job)
        started = time.time()

        def progress(p: float, stage: str) -> None:
            job["progress"] = round(p, 3)
            job["stage"] = stage
            el = time.time() - started
            job["eta_sec"] = round(el / p - el) if p > 0.05 else None
            job["updated_at"] = _now()
            JobStore.save(job)

        params = job["params"]
        last_err = None
        for attempt in range(2):  # retry при сбоях (сеть, TTS)
            try:
                if params.get("book_id"):
                    book_id = params["book_id"]
                else:
                    book_id = pipeline.prepare_book(params, progress)["book_id"]
                job["book_id"] = book_id
                job["book"] = {k: v for k, v in pipeline.book_summary(book_id).items()
                               if k in ("title", "author", "source", "source_url", "license_note", "words")}
                result = pipeline.render(JOBS / job_id, book_id, params.get("settings") or {}, progress)
                job.update(status="done", result=result, progress=1.0, stage="Готово", finished_at=_now(), eta_sec=0)
                JobStore.save(job)
                break
            except SourceError as e:
                job.update(status="failed", error={"code": e.code, "message": e.message, "suggestions": e.suggestions},
                           finished_at=_now(), stage="Ошибка")
                JobStore.save(job)
                break
            except Exception as e:
                last_err = e
                log.exception("job %s failed (attempt %s)", job_id, attempt + 1)
                if attempt == 1:
                    job.update(status="failed", finished_at=_now(), stage="Ошибка",
                               error={"code": "internal", "message": str(e), "trace": traceback.format_exc()[-2000:]})
                    JobStore.save(job)
        self._webhook(job)

    @staticmethod
    def _webhook(job: dict) -> None:
        url = (job.get("params") or {}).get("webhook_url")
        if not url:
            return
        payload = {k: job.get(k) for k in ("id", "status", "progress", "result", "error", "book")}
        for attempt in range(3):
            try:
                r = httpx.post(url, json=payload, timeout=15)
                if r.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2 ** attempt)


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue(config.WORKERS)
    return _queue
