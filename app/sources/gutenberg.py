"""Project Gutenberg через открытый API gutendex.com (только public domain)."""
from __future__ import annotations

import re

from app.models import Book, SourceError
from app.sources.http import fetch, fetch_json
from app.sources.normalize import decode_bytes, normalize_text
from app.text.structure import split_chapters

API = "https://gutendex.com/books"


def search(query: str, language: str | None = None) -> list[dict]:
    params = {"search": query}
    if language:
        params["languages"] = language
    data = fetch_json(API, params)
    return [b for b in data.get("results", []) if not b.get("copyright")]


def _strip_pg_boilerplate(text: str) -> str:
    start = re.search(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I | re.S)
    end = re.search(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", text, re.I)
    s = start.end() if start else 0
    e = end.start() if end else len(text)
    return text[s:e]


def fetch_book(meta: dict) -> Book:
    formats = meta.get("formats", {})
    url = next((u for k, u in formats.items() if k.startswith("text/plain")), None)
    if not url:
        raise SourceError("У книги на Gutenberg нет текстовой версии", code="no_text")
    raw, _, final = fetch(url)
    text = normalize_text(_strip_pg_boilerplate(decode_bytes(raw)))
    # в txt Gutenberg абзацы разделены пустой строкой, строки — жёстко перенесены
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    authors = ", ".join(a.get("name", "") for a in meta.get("authors", []))
    return Book(
        title=meta.get("title", ""), author=authors, language=(meta.get("languages") or ["en"])[0],
        source="gutenberg", source_url=f"https://www.gutenberg.org/ebooks/{meta.get('id')}",
        license_note="Public domain in the USA (Project Gutenberg)",
        chapters=split_chapters(text, default_title=meta.get("title", "Глава 1")),
    )


def find(title: str, author: str = "", language: str | None = None) -> Book:
    results = search(f"{title} {author}".strip(), language) or search(title, language)
    if not results:
        raise SourceError("Не найдено на Project Gutenberg", code="not_found")
    return fetch_book(results[0])
