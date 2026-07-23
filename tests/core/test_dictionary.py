from privacyprotection.core.dictionary import DictionaryDetector

def test_detects_registered_words():
    det = DictionaryDetector({"株式会社サンプル": "CUSTOM", "PRJ-001": "CUSTOM"})
    r = det.detect("株式会社サンプルのPRJ-001について")
    assert [(d.text, d.category, d.source) for d in r] == [
        ("株式会社サンプル", "CUSTOM", "dictionary"),
        ("PRJ-001", "CUSTOM", "dictionary"),
    ]

def test_longer_entry_wins_over_substring():
    det = DictionaryDetector({"山田": "PERSON", "山田製作所": "ORG"})
    r = det.detect("山田製作所に連絡")
    assert [(d.text, d.category) for d in r] == [("山田製作所", "ORG")]

def test_multiple_occurrences_all_detected():
    det = DictionaryDetector({"山田": "PERSON"})
    assert len(det.detect("山田と山田")) == 2

def test_empty_dictionary():
    assert DictionaryDetector({}).detect("なにか") == []
