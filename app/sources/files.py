"""Парсинг загруженных файлов: TXT, FB2, EPUB, DOCX, PDF (текстовый слой; OCR — опционально)."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from app.models import Book, Chapter, SourceError
from app.sources.normalize import decode_bytes, normalize_text
from app.text.structure import split_chapters

SUPPORTED = (".txt", ".fb2", ".epub", ".docx", ".pdf", ".zip")


def parse_file(filename: str, data: bytes) -> Book:
    name = filename.lower()
    stem = Path(filename).stem
    if name.endswith(".zip") or (data[:2] == b"PK" and name.endswith(".fb2.zip")):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            inner = next((n for n in z.namelist() if n.lower().endswith(SUPPORTED[:-1])), None)
            if not inner:
                raise SourceError("В архиве нет поддерживаемых файлов", code="bad_file")
            return parse_file(inner, z.read(inner))
    if name.endswith(".fb2"):
        return parse_fb2(data, stem)
    if name.endswith(".epub"):
        return parse_epub(data, stem)
    if name.endswith(".docx"):
        return parse_docx(data, stem)
    if name.endswith(".pdf"):
        return parse_pdf(data, stem)
    if name.endswith(".txt") or not Path(name).suffix:
        text = normalize_text(decode_bytes(data))
        return Book(title=stem, source="file", chapters=split_chapters(text, default_title=stem))
    raise SourceError(f"Формат не поддерживается. Поддерживаются: {', '.join(SUPPORTED)}", code="bad_format")


def parse_fb2(data: bytes, stem: str = "Книга") -> Book:
    soup = BeautifulSoup(decode_bytes(data), "xml")
    ti = soup.find("title-info")
    title = ti.find("book-title").get_text(strip=True) if ti and ti.find("book-title") else stem
    author = ""
    if ti and ti.find("author"):
        a = ti.find("author")
        author = " ".join(x.get_text(strip=True) for x in a.find_all(["first-name", "last-name"]))
    lang = ti.find("lang").get_text(strip=True) if ti and ti.find("lang") else "ru"
    chapters: list[Chapter] = []
    body = soup.find("body")
    if body is None:
        raise SourceError("Некорректный FB2: нет <body>", code="bad_file")
    for note in soup.find_all("body", attrs={"name": "notes"}):
        note.decompose()
    sections = body.find_all("section", recursive=False) or [body]
    for i, sec in enumerate(sections):
        # вложенные секции → отдельные главы
        subs = sec.find_all("section", recursive=False)
        for s in subs or [sec]:
            t = s.find("title")
            ct = t.get_text(" ", strip=True) if t else f"Глава {len(chapters) + 1}"
            if t:
                t.decompose()
            paras = [p.get_text(" ", strip=True) for p in s.find_all(["p", "v"])]
            text = normalize_text("\n".join(p for p in paras if p))
            if text:
                chapters.append(Chapter(ct, text))
    return Book(title=title, author=author, language=lang, source="file", chapters=chapters)


def parse_epub(data: bytes, stem: str = "Книга") -> Book:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if any("encryption.xml" in n for n in names):
            enc = z.read(next(n for n in names if "encryption.xml" in n)).decode("utf-8", "ignore")
            if "EncryptedData" in enc and "font" not in enc.lower():
                raise SourceError("Файл защищён DRM. Сервис не снимает защиту — используйте файл без DRM.", code="drm")
        container = BeautifulSoup(z.read("META-INF/container.xml"), "xml")
        opf_path = container.find("rootfile")["full-path"]
        opf = BeautifulSoup(z.read(opf_path), "xml")
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        title = opf.find("dc:title").get_text(strip=True) if opf.find("dc:title") else stem
        author = opf.find("dc:creator").get_text(strip=True) if opf.find("dc:creator") else ""
        lang = opf.find("dc:language").get_text(strip=True)[:2] if opf.find("dc:language") else "ru"
        manifest = {i["id"]: i["href"] for i in opf.find_all("item") if i.get("id") and i.get("href")}
        chapters: list[Chapter] = []
        for ref in opf.find_all("itemref"):
            href = manifest.get(ref.get("idref"))
            if not href:
                continue
            try:
                html = z.read(base + href)
            except KeyError:
                continue
            s = BeautifulSoup(html, "lxml")
            h = s.find(["h1", "h2", "h3"])
            ct = h.get_text(" ", strip=True) if h else f"Глава {len(chapters) + 1}"
            if h:
                h.decompose()
            paras = [p.get_text(" ", strip=True) for p in s.find_all(["p", "div"]) if not p.find(["p", "div"])]
            text = normalize_text("\n".join(p for p in paras if p))
            if len(text) > 50:
                chapters.append(Chapter(ct, text))
    return Book(title=title, author=author, language=lang, source="file", chapters=chapters)


def parse_docx(data: bytes, stem: str = "Документ") -> Book:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(data))
    chapters: list[Chapter] = []
    cur_title, cur = stem, []
    for p in d.paragraphs:
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith(("heading", "заголовок")) and p.text.strip():
            if cur:
                chapters.append(Chapter(cur_title, normalize_text("\n".join(cur))))
            cur_title, cur = p.text.strip(), []
        elif p.text.strip():
            cur.append(p.text)
    if cur:
        chapters.append(Chapter(cur_title, normalize_text("\n".join(cur))))
    if len(chapters) == 1:
        chapters = split_chapters(chapters[0].text, default_title=stem)
    title = d.core_properties.title or stem
    return Book(title=title, author=d.core_properties.author or "", source="file", chapters=chapters)


def parse_pdf(data: bytes, stem: str = "Документ") -> Book:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise SourceError("PDF зашифрован. Сервис не снимает защиту.", code="drm")
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    text = "\n".join(pages)
    warnings = []
    if len(text.strip()) < 100 * max(1, len(pages)) * 0.2:
        ocr = _ocr_pdf(data)
        if ocr:
            text, warnings = ocr, ["Текст распознан OCR — возможны ошибки"]
        else:
            raise SourceError("В PDF нет текстового слоя, а OCR (tesseract) не установлен", code="ocr_unavailable")
    # склейка строк внутри абзаца
    text = re.sub(r"(?<![.!?…:»])\n(?=[а-яёa-z])", " ", text)
    text = normalize_text(text)
    info = reader.metadata or {}
    return Book(title=(info.get("/Title") or stem), author=info.get("/Author") or "", source="file",
                chapters=split_chapters(text, default_title=stem), warnings=warnings)


def _ocr_pdf(data: bytes) -> str:
    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        return ""
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "in.pdf"
        pdf.write_bytes(data)
        subprocess.run(["pdftoppm", "-r", "200", "-png", str(pdf), f"{td}/p"], check=True)
        out = []
        for img in sorted(Path(td).glob("p*.png")):
            r = subprocess.run(["tesseract", str(img), "-", "-l", "rus+eng"], capture_output=True, text=True)
            out.append(r.stdout)
        return "\n".join(out)
