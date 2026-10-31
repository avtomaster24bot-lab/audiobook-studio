from app.text.attribution import merge_false_remarks
from app.text.structure import segment_chapter, split_chapters, split_dialogue_paragraph


def test_split_chapters_roman_and_glava():
    text = "Глава I\nПервый абзац длинный " + "текст " * 80 + "\nГлава II\nВторой " + "текст " * 80
    chs = split_chapters(text)
    assert [c.title for c in chs] == ["Глава I", "Глава II"]
    text2 = "I\n" + "а " * 200 + "\nII\n" + "б " * 200 + "\nIII\n" + "в " * 200
    assert len(split_chapters(text2)) == 3


def test_no_false_chapters():
    assert len(split_chapters("Просто рассказ без глав.\nВторой абзац.")) == 1


def test_dialogue_split():
    parts = split_dialogue_paragraph("— Никак беспорядок, ваше благородие!.. — говорит городовой.")
    assert parts == [("dialogue", "Никак беспорядок, ваше благородие!.."), ("narration", "говорит городовой.")]
    parts = split_dialogue_paragraph("— Это не наша, — продолжает Прохор. — Это генералова брата.")
    assert [k for k, _ in parts] == ["dialogue", "narration", "dialogue"]


def test_false_remark_merged_back():
    segs = merge_false_remarks(segment_chapter(
        "— Иду я, никого не трогаю… — начинает Хрюкин. — Насчёт дров, — и вдруг эта подлая за палец…"))
    kinds = [s.kind for s in segs]
    assert kinds == ["dialogue", "narration", "dialogue"]
    assert "и вдруг эта подлая" in segs[2].text


def test_narration_paragraph():
    segs = segment_chapter("Кругом тишина… На площади ни души…")
    assert len(segs) == 1 and segs[0].kind == "narration"
