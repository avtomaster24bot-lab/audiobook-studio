"""Метаданные: Open Library и Google Books (по ISBN или названию+автору)."""
from __future__ import annotations

from app.models import SourceError
from app.sources.http import fetch_json


def by_isbn(isbn: str) -> dict:
    isbn = "".join(ch for ch in isbn if ch.isdigit() or ch in "Xx")
    data = fetch_json("https://openlibrary.org/api/books", {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"})
    item = data.get(f"ISBN:{isbn}")
    if item:
        return {
            "title": item.get("title", ""),
            "author": ", ".join(a.get("name", "") for a in item.get("authors", [])),
            "cover_url": (item.get("cover") or {}).get("large", ""),
            "year": item.get("publish_date", ""),
            "source": "openlibrary",
        }
    g = fetch_json("https://www.googleapis.com/books/v1/volumes", {"q": f"isbn:{isbn}"})
    for it in g.get("items", []):
        v = it.get("volumeInfo", {})
        return {
            "title": v.get("title", ""), "author": ", ".join(v.get("authors", [])),
            "cover_url": (v.get("imageLinks") or {}).get("thumbnail", ""),
            "year": v.get("publishedDate", ""), "language": v.get("language", ""), "source": "googlebooks",
        }
    raise SourceError("Книга с таким ISBN не найдена в Open Library и Google Books", code="isbn_not_found")


def search(title: str, author: str = "") -> list[dict]:
    params = {"title": title, "limit": 5}
    if author:
        params["author"] = author
    data = fetch_json("https://openlibrary.org/search.json", params)
    out = []
    for d in data.get("docs", []):
        out.append({
            "title": d.get("title", ""), "author": ", ".join(d.get("author_name", [])[:2]),
            "year": d.get("first_publish_year", ""),
        })
    return out
