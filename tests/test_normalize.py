from app.sources.normalize import decode_bytes, normalize_text, text_hash


def test_dashes_quotes_spaces():
    t = normalize_text('- Привет,\u00a0- сказал он. "Ну" и „ладно“...\r\nсло-\nво')
    assert t.startswith("— Привет, — сказал он.")
    assert "«Ну»" in t and "«ладно»" in t
    assert "…" in t
    assert "слово" in t


def test_decode_cp1251_and_koi8():
    s = "Через базарную площадь идёт полицейский надзиратель"
    assert decode_bytes(s.encode("cp1251")) == s
    assert decode_bytes(s.encode("koi8-r")) == s
    assert decode_bytes(s.encode("utf-8")) == s


def test_mojibake_fixed():
    s = "Привет, мир"
    broken = s.encode("utf-8").decode("cp1251")
    assert decode_bytes(broken.encode("utf-8")) == s


def test_hash_stable_under_whitespace():
    assert text_hash("А  б\nв") == text_hash("а б в")
