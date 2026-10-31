"""Единая точка входа модуля источников: файл / название+автор / свободный запрос / ссылка / ISBN."""
from __future__ import annotations

import hashlib
import json
import re

from app import config
from app.models import Book, SourceError
from app.sources import files, gutenberg, metadata, web, wikisource
from app.sources.normalize import count_words, text_hash

CACHE = config.DATA_DIR / "cache" / "books"
CACHE.mkdir(parents=True, exist_ok=True)

ISBN_RE = re.compile(r"^(?:ISBN[:\s-]*)?((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])$", re.I)


def _is_cyrillic(s: str) -> bool:
    return bool(re.search(r"[а-яё]", s, re.I))


def split_query(query: str) -> tuple[str, str]:
    """«Хамелеон Чехов» / «Чехов — Хамелеон» / «Хамелеон, А. П. Чехов» → (title, author)."""
    from app.text import llm

    q = query.strip().strip("«»\"")
    if llm.enabled():
        res = llm.parse_book_query(q)
        if res and res.get("title"):
            return res["title"], res.get("author", "")
    for sep in (" — ", " - ", ", ", ": "):
        if sep in q:
            a, b = q.split(sep, 1)
            # автор обычно короче и содержит инициалы/известную фамилию
            if re.search(r"\b[А-ЯA-Z]\.\s?", b) or len(b.split()) <= 3 and len(a.split()) > len(b.split()):
                return a.strip(), b.strip()
            return b.strip(), a.strip()
    return q, ""


def _cache_key(**kw) -> str:
    return hashlib.sha1(json.dumps(kw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _finalize(book: Book) -> Book:
    words = count_words(book.full_text)
    if words > config.MAX_WORDS:
        book.warnings.append(f"Очень большая книга ({words} слов): будет обработана частями, подтвердите стоимость/время.")
    if book.language == "ru":
        latin = len(re.findall(r"[a-z]", book.full_text[:20000], re.I))
        if latin > 2000:
            book.warnings.append("В тексте много латиницы: возможна многоязычность, основной язык — русский.")
    return book


def resolve(*, query: str = "", title: str = "", author: str = "", url: str = "", isbn: str = "",
            filename: str = "", data: bytes | None = None) -> Book:
    if data is not None:
        return _finalize(files.parse_file(filename or "book.txt", data))

    key = _cache_key(query=query, title=title, author=author, url=url, isbn=isbn)
    cp = CACHE / f"{key}.json"
    if cp.exists():
        return Book.from_dict(json.loads(cp.read_text()))

    if query and not (title or url or isbn):
        q = query.strip()
        if re.match(r"^https?://", q):
            url = q
        elif ISBN_RE.match(q.replace(" ", "")):
            isbn = q
        else:
            title, author = split_query(q)

    if url:
        book = web.from_url(url)
    else:
        meta = {}
        if isbn:
            meta = metadata.by_isbn(isbn)
            title, author = meta.get("title", ""), meta.get("author", "")
        if not title:
            raise SourceError("Укажите название, ссылку, ISBN или загрузите файл", code="empty_query")
        book = None
        errors = []
        order = ["wikisource", "gutenberg"] if _is_cyrillic(title + author) else ["gutenberg", "wikisource"]
        for src in order:
            try:
                if src == "wikisource":
                    book = wikisource.find(title, author)
                else:
                    book = gutenberg.find(title, author, "ru" if _is_cyrillic(title) else None)
                break
            except SourceError as e:
                errors.append(e)
        if book is None:
            suggestions = []
            try:
                suggestions = [f"{m['title']} — {m['author']}" for m in metadata.search(title, author)]
            except SourceError:
                pass
            raise SourceError(
                "Книга не найдена в открытых источниках (Викитека, Project Gutenberg). "
                "Если произведение ещё под авторским правом — загрузите легально приобретённый файл. "
                "Уточните автора или укажите ISBN.",
                code="not_found", suggestions=suggestions,
            )
        if meta.get("cover_url"):
            book.cover_url = meta["cover_url"]
    book = _finalize(book)
    cp.write_text(json.dumps(book.to_dict(), ensure_ascii=False))
    return book


def book_fingerprint(book: Book) -> str:
    return text_hash(book.full_text)
