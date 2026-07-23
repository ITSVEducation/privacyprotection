import pytest
from privacyprotection.handlers.text_handler import TextHandler

h = TextHandler()

def _roundtrip(tmp_path, name, data: bytes):
    src = tmp_path / name
    src.write_bytes(data)
    frags = h.read_fragments(src)
    dst = tmp_path / f"out_{name}"
    h.write_fragments(src, dst, frags)  # 無加工で書き戻し
    return src.read_bytes(), dst.read_bytes()

def test_utf8_roundtrip_byte_identical(tmp_path):
    a, b = _roundtrip(tmp_path, "a.txt", "山田太郎\nです\n".encode("utf-8"))
    assert a == b

def test_utf8_bom_preserved(tmp_path):
    a, b = _roundtrip(tmp_path, "b.txt", "﻿山田\r\n".encode("utf-8"))
    assert a == b

def test_cp932_roundtrip_byte_identical(tmp_path):
    a, b = _roundtrip(tmp_path, "c.txt", "山田太郎です\r\n".encode("cp932"))
    assert a == b

def test_masked_text_written_in_original_encoding(tmp_path):
    src = tmp_path / "d.txt"
    src.write_bytes("山田太郎".encode("cp932"))
    frags = h.read_fragments(src)
    frags[0].text = "【人名_1】"
    dst = tmp_path / "d_masked.txt"
    h.write_fragments(src, dst, frags)
    assert dst.read_bytes() == "【人名_1】".encode("cp932")

def test_undecodable_raises(tmp_path):
    src = tmp_path / "bin.txt"
    src.write_bytes(b"\xff\xfe\x00\x01\x02\xff\xff\xff")
    with pytest.raises(UnicodeError):
        h.read_fragments(src)
