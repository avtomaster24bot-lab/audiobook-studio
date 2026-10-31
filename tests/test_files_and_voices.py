import io
import zipfile

from app.sources.files import parse_file
from app.tts.voices import all_voices, assign_voices

FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<author><first-name>Антон</first-name><last-name>Чехов</last-name></author><book-title>Тест</book-title><lang>ru</lang>
</title-info></description><body>
<section><title><p>Глава 1</p></title><p>— Здравствуйте, — сказал Иван.</p><p>Кругом тишина.</p></section>
<section><title><p>Глава 2</p></title><p>Второй абзац.</p></section>
</body></FictionBook>"""


def test_fb2():
    b = parse_file("book.fb2", FB2.encode("utf-8"))
    assert b.title == "Тест" and b.author == "Антон Чехов"
    assert [c.title for c in b.chapters] == ["Глава 1", "Глава 2"]
    assert b.chapters[0].text.startswith("— Здравствуйте")


def _epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<metadata><dc:title>Эпаб</dc:title><dc:creator>Автор</dc:creator><dc:language>ru</dc:language></metadata>'
                   '<manifest><item id="c1" href="c1.xhtml"/></manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/c1.xhtml", "<html><body><h1>Первая</h1><p>— Да, — ответила Мария. Это длинный абзац текста.</p>"
                   "<p>Ещё один абзац, чтобы глава была не пустой.</p></body></html>")
    return buf.getvalue()


def test_epub():
    b = parse_file("x.epub", _epub())
    assert b.title == "Эпаб" and b.chapters[0].title == "Первая"
    assert "ответила Мария" in b.chapters[0].text


def test_txt_cp1251():
    b = parse_file("rasskaz.txt", "Глава I\n".encode("cp1251") + ("Текст главы. " * 50).encode("cp1251"))
    assert b.chapters[0].title == "Глава I"


def test_at_least_10_voices_and_consistent_assignment():
    assert len(all_voices()) >= 10
    chars = [{"name": f"P{i}", "gender": "male" if i % 2 else "female"} for i in range(8)]
    m1, m2 = assign_voices(chars), assign_voices(chars)
    assert m1 == m2
    vals = [m1[c["name"]] for c in chars]
    assert len(set(vals)) == len(vals)          # без повторов
    assert m1["narrator"] not in vals           # рассказчик — отдельный голос
