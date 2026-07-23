import pytest

spacy = pytest.importorskip("spacy")
try:
    spacy.load("ja_ginza")
except OSError:
    pytest.skip("ja_ginza model not installed", allow_module_level=True)

from privacyprotection.core.ner import NerDetector


def test_detects_person_and_org_as_detection_objects():
    det = NerDetector()
    r = det.detect("山田太郎は株式会社サンプルに勤務している。")
    assert all(d.source == "ner" for d in r)
    assert all(d.category in {"PERSON", "ORG", "LOC"} for d in r)
    assert any(d.category == "PERSON" for d in r)
    # start/end がテキストと整合していること
    for d in r:
        assert "山田太郎は株式会社サンプルに勤務している。"[d.start:d.end] == d.text


def test_plain_text_returns_list():
    assert isinstance(NerDetector().detect("12345"), list)


def test_detects_all_three_categories_and_spans_match_text():
    # 山田太郎→PERSON, 株式会社サンプル→ORG, 東京都→LOC の3カテゴリすべてが
    # 実際に検出されること、かつ各 Detection.text が原文の [start:end] と
    # 完全に一致することを固定する。
    det = NerDetector()
    text = "山田太郎は株式会社サンプルに勤務している。東京都に住んでいる。"
    r = det.detect(text)
    assert all(d.source == "ner" for d in r)
    categories = {d.category for d in r}
    assert categories <= {"PERSON", "ORG", "LOC"}
    assert "PERSON" in categories
    assert "ORG" in categories
    assert "LOC" in categories
    for d in r:
        assert text[d.start:d.end] == d.text
