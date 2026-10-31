"""Загрузка по ссылке: HTML-страница (trafilatura), оглавление, прямые файлы EPUB/FB2/PDF/TXT."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models import Book, Chapter, SourceError
from app.sources import files, wikisource
from app.sources.http import fetch, looks_paywalled
from app.sources.normalize import decode_bytes, normalize_text
from app.text.structure import split_chapters

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

FILE_EXT = (".epub", ".fb2", ".pdf", ".txt", ".docx", ".zip")


def _meta(soup: BeautifulSoup) -> dict:
    def m(*names):
        for n in names:
            el = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if el and el.get("content"):
                return el["content"].strip()
        return ""
    title = m("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
    return {
        "title": title, "author": m("author", "book:author", "article:author"),
        "cover_url": m("og:image"), "language": (soup.html.get("lang") if soup.html else "") or "",
    }


def _extract_main(html: str, url: str) -> str:
    text = ""
    if trafilatura is not None:
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False,
                                   favor_recall=True, deduplicate=True) or ""
    if len(text) < 500:
        soup = BeautifulSoup(html, "lxml")
        for el in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            el.decompose()
        # lib.ru и подобные: текст в <pre>
        pre = soup.find_all("pre")
        cand = "\n".join(p.get_text("\n") for p in pre) if pre else soup.get_text("\n")
        if len(cand) > len(text):
            text = cand
    return normalize_text(text)


def _toc_links(html: str, url: str) -> list[str]:
    """Ссылки оглавления: однотипные ссылки на тот же сайт, содержащие «глава»/номер."""
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True).lower()
        href = urljoin(url, a["href"])
        if urlparse(href).netloc != host or href == url or "#" in href:
            continue
        if re.match(r"^(глава|часть|chapter)\s+\w+", t) or re.match(r"^[ivxlc]+\.?$", t):
            if href not in links:
                links.append(href)
    return links


def from_url(url: str) -> Book:
    if not re.match(r"^https?://", url):
        raise SourceError("Ссылка должна начинаться с http:// или https://", code="bad_url")
    host = urlparse(url).netloc
    if host.endswith("wikisource.org"):
        title = url.split("/wiki/", 1)[-1]
        from urllib.parse import unquote
        return wikisource.fetch_work(unquote(title).replace("_", " "))
    raw, ctype, final = fetch(url)
    path = urlparse(final).path.lower()
    if path.endswith(FILE_EXT) or any(x in ctype for x in ("epub", "fb2", "pdf", "octet-stream", "zip", "officedocument")):
        book = files.parse_file(path.rsplit("/", 1)[-1] or "book", raw)
        book.source, book.source_url = "url", final
        return book
    html = decode_bytes(raw)
    if looks_paywalled(html):
        raise SourceError(
            "Похоже, текст на странице доступен только по подписке или после покупки. "
            "Сервис не обходит платные стены — загрузите легально приобретённый файл.",
            code="paywall",
        )
    soup = BeautifulSoup(html, "lxml")
    meta = _meta(soup)
    main = _extract_main(html, final)
    toc = _toc_links(html, final)
    chapters: list[Chapter]
    if len(toc) >= 2 and len(main) < 20000:
        chapters = []
        for i, link in enumerate(toc[:300]):
            r, _, f = fetch(link)
            h = decode_bytes(r)
            t = _extract_main(h, f)
            s = BeautifulSoup(h, "lxml")
            ct = (s.find(["h1", "h2"]).get_text(strip=True) if s.find(["h1", "h2"]) else f"Глава {i + 1}")
            if t:
                chapters.append(Chapter(ct, t))
        # дедупликация повторов при пагинации
        seen, uniq = set(), []
        for c in chapters:
            k = c.text[:500]
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        chapters = uniq
    else:
        chapters = split_chapters(main, default_title=meta["title"] or "Глава 1")
    if not chapters or sum(len(c.text) for c in chapters) < 200:
        raise SourceError("На странице не найден текст произведения", code="empty")
    return Book(
        title=meta["title"] or chapters[0].title, author=meta["author"],
        language=(meta["language"] or "ru")[:2], source="url", source_url=final,
        cover_url=meta["cover_url"],
        license_note="Пользователь подтверждает право на использование текста по ссылке",
        chapters=chapters,
    )
