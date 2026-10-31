"""Поиск и загрузка произведений с ru.wikisource.org (общественное достояние) через MediaWiki API."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import Book, Chapter, SourceError
from app.sources.http import fetch_json
from app.sources.normalize import normalize_text
from app.text.structure import split_chapters

API = "https://ru.wikisource.org/w/api.php"
PD_NOTE = "Общественное достояние (ru.wikisource.org, ст. 1281 ГК РФ)"
REMOVE_SELECTORS = [
    "table", ".ws-noexport", ".noprint", ".mw-editsection", ".reference", ".references",
    "sup.reference", ".navbox", ".header_notes", ".ws-header", "#headertemplate", ".licenseContainer",
    ".mw-references-wrap", "style", "script", ".infobox", ".poem-number",
]


def search(query: str, limit: int = 10) -> list[str]:
    data = fetch_json(API, {
        "action": "query", "list": "search", "srsearch": query, "format": "json",
        "srlimit": limit, "srnamespace": 0,
    })
    return [h["title"] for h in data.get("query", {}).get("search", [])]


def parse_page(title: str) -> tuple[str, str]:
    data = fetch_json(API, {"action": "parse", "page": title, "prop": "text", "format": "json", "redirects": 1})
    if "error" in data:
        raise SourceError(f"Страница «{title}» не найдена в Викитеке", code="not_found")
    return data["parse"]["title"], data["parse"]["text"]["*"]


def _edition_links(html: str) -> list[str]:
    """Если страница — список редакций, возвращает ссылки на редакции."""
    soup = BeautifulSoup(html, "lxml")
    if "список редакций" not in soup.get_text():
        return []
    links = []
    for a in soup.select("a[href^='/wiki/']"):
        t = a.get("title") or ""
        if "/" in t and not t.startswith(("Викитека:", "Категория:", "Служебная:", "Обсуждение")):
            links.append(t)
    return links


def _subpage_links(title: str, html: str) -> list[str]:
    """Оглавление: ссылки на подстраницы «Название/Глава I»."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select(".mw-parser-output a[href^='/wiki/']"):
        t = a.get("title") or ""
        if t.startswith(title + "/") and t not in out and "Версия" not in t and "Вариант" not in t:
            out.append(t)
    return out


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".mw-parser-output") or soup
    for sel in REMOVE_SELECTORS:
        for el in root.select(sel):
            el.decompose()
    blocks = []
    for el in root.find_all(["p", "h2", "h3", "h4", "div"], recursive=True):
        if el.name == "div" and "poem" not in (el.get("class") or []):
            continue
        txt = el.get_text(" ", strip=False)
        txt = re.sub(r"\[\d+\]|\[править\]", "", txt)
        txt = re.sub(r"[ \t]+", " ", txt).strip()
        if not txt:
            continue
        if el.name in ("h2", "h3", "h4"):
            blocks.append("\n" + txt + "\n")
        else:
            blocks.append(txt)
    text = normalize_text("\n".join(blocks))
    return strip_back_matter(text)


BACK_MATTER = re.compile(r"(?mi)^(примечания|комментарии|см\. также|ссылки|источники|литература)\s*$")


def strip_back_matter(text: str) -> str:
    """Сноски/примечания выносим за пределы озвучиваемого текста."""
    m = BACK_MATTER.search(text)
    if m and m.start() > len(text) * 0.5:
        return text[: m.start()].rstrip()
    return text


def _score_text(text: str) -> float:
    # предпочитаем современную орфографию (без «ѣ», «і») и более полный текст
    old = len(re.findall(r"[ѣіѳѵ]|ъ\b", text))
    return len(text) - old * 50


def fetch_work(title: str, author: str = "") -> Book:
    page_title, html = parse_page(title)
    candidates: list[tuple[str, str]] = []
    editions = _edition_links(html)
    if editions:
        for ed in editions[:5]:
            try:
                candidates.append(parse_page(ed))
            except SourceError:
                continue
    else:
        candidates.append((page_title, html))

    best_title, best_text, best_bonus = None, "", 0
    for t, h in candidates:
        subs = _subpage_links(t, h)
        own = extract_text(h)
        if len(subs) >= 2 and len(own) < 3000:
            parts = []
            for s in subs[:200]:
                st, sh = parse_page(s)
                parts.append(f"\n{st.split('/')[-1]}\n{extract_text(sh)}")
            text = normalize_text("\n".join(parts))
        else:
            text = own
        # каноническая редакция (ПСС) предпочтительнее журнальных вариантов
        bonus = 5000 if re.search(r"ПСС|Полное собрание", t) else 0
        if _score_text(text) + bonus > _score_text(best_text) + best_bonus:
            best_bonus = bonus
            best_title, best_text = t, text
    if not best_text or len(best_text) < 200:
        raise SourceError("В Викитеке не удалось извлечь текст произведения", code="empty")

    clean_title = re.sub(r"\s*\(.*?\)\s*$", "", best_title.split("/")[0]).strip()
    m = re.search(r"\(([^)]+)\)", best_title.split("/")[0])
    book_author = author or (m.group(1) if m else "")
    chapters = split_chapters(best_text, default_title=clean_title)
    return Book(
        title=clean_title, author=book_author, language="ru", source="wikisource",
        source_url="https://ru.wikisource.org/wiki/" + best_title.replace(" ", "_"),
        license_note=PD_NOTE, chapters=chapters,
    )


def find(title: str, author: str = "") -> Book:
    q = f"{title} {author}".strip()
    hits = search(q) or search(title)
    if not hits:
        raise SourceError("Произведение не найдено в Викитеке", code="not_found")
    qwords = [w[:5] for w in re.findall(r"\w+", f"{title} {author}".lower()) if len(w) > 1]
    twords = [w[:5] for w in re.findall(r"\w+", title.lower())]

    def rank(h: str) -> tuple:
        base = h.split("/")[0].lower()
        bwords = [w[:5] for w in re.findall(r"\w+", base)]
        overlap = sum(1 for w in qwords if w in bwords)
        head = bwords[0] == twords[0] if bwords and twords else False
        return (overlap, head, "/" not in h, -len(h))

    hits.sort(key=rank, reverse=True)
    last_err: SourceError | None = None
    for h in hits[:3]:
        try:
            return fetch_work(h.split("/")[0] if "/Версия" in h or "/Вариант" in h else h, author)
        except SourceError as e:
            last_err = e
    raise last_err or SourceError("Произведение не найдено", code="not_found")
