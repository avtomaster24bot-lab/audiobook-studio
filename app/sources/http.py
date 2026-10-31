"""Вежливый HTTP-клиент: robots.txt, Crawl-delay, User-Agent, кэш на диске."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app import config
from app.models import SourceError

_CACHE = config.DATA_DIR / "cache" / "http"
_CACHE.mkdir(parents=True, exist_ok=True)
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}
_lock = threading.Lock()

# Площадки с открытым API: обращения к API не являются краулингом страниц
API_HOSTS = {"ru.wikisource.org", "gutendex.com", "openlibrary.org", "www.googleapis.com", "covers.openlibrary.org"}

PAYWALL_MARKERS = (
    "paywall", "subscribe to continue", "купите книгу, чтобы", "доступно по подписке",
    "чтобы продолжить чтение", "купить и скачать", "ознакомительный фрагмент", "litres.ru/pages/biblio_book",
)


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        follow_redirects=True,
        timeout=30,
    )


def _robots_for(url: str) -> urllib.robotparser.RobotFileParser | None:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base in _robots:
        return _robots[base]
    rp = urllib.robotparser.RobotFileParser()
    try:
        with client() as c:
            r = c.get(base + "/robots.txt")
        if r.status_code >= 400:
            rp = None  # нет robots.txt → разрешено
        else:
            rp.parse(r.text.splitlines())
    except httpx.HTTPError:
        rp = None
    _robots[base] = rp
    return rp


def allowed(url: str) -> bool:
    host = urlparse(url).netloc
    if host in API_HOSTS:
        return True
    rp = _robots_for(url)
    return True if rp is None else rp.can_fetch(config.USER_AGENT, url)


def _respect_delay(url: str) -> None:
    host = urlparse(url).netloc
    rp = _robots.get(f"{urlparse(url).scheme}://{host}")
    delay = config.CRAWL_DELAY_SEC
    if rp is not None:
        try:
            d = rp.crawl_delay(config.USER_AGENT)
            if d:
                delay = max(delay, float(d))
        except Exception:
            pass
    with _lock:
        wait = _last_hit.get(host, 0) + delay - time.time()
        if wait > 0:
            time.sleep(min(wait, 10))
        _last_hit[host] = time.time()


def fetch(url: str, params: dict | None = None, *, use_cache: bool = True, check_robots: bool = True) -> tuple[bytes, str, str]:
    """Возвращает (content, content_type, final_url). Бросает SourceError при запрете/ошибке."""
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    meta_path, body_path = _CACHE / f"{key}.json", _CACHE / f"{key}.bin"
    if use_cache and meta_path.exists() and body_path.exists():
        meta = json.loads(meta_path.read_text())
        return body_path.read_bytes(), meta["ctype"], meta["url"]
    if check_robots and not allowed(url):
        raise SourceError(
            "Сайт запрещает автоматическую загрузку этой страницы (robots.txt). "
            "Скачайте файл вручную и загрузите его в сервис.",
            code="robots_disallowed",
        )
    _respect_delay(url)
    try:
        with client() as c:
            r = c.get(url, params=params)
    except httpx.HTTPError as e:
        raise SourceError(f"Не удалось загрузить {url}: {e}", code="network") from e
    if r.status_code in (401, 402, 403):
        raise SourceError(
            "Источник закрыт (требуется вход, оплата или сайт блокирует автоматический доступ). "
            "Мы не обходим платные стены и защиту — загрузите легально приобретённый файл.",
            code="forbidden",
        )
    if r.status_code == 429:
        raise SourceError("Источник ограничил частоту запросов. Попробуйте позже.", code="rate_limited")
    if r.status_code >= 400:
        raise SourceError(f"Источник вернул ошибку HTTP {r.status_code}", code="http_error")
    ctype = r.headers.get("content-type", "")
    if use_cache:
        body_path.write_bytes(r.content)
        meta_path.write_text(json.dumps({"ctype": ctype, "url": str(r.url)}))
    return r.content, ctype, str(r.url)


def fetch_json(url: str, params: dict | None = None) -> dict:
    body, _, _ = fetch(url, params)
    return json.loads(body.decode("utf-8"))


def looks_paywalled(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in PAYWALL_MARKERS)
