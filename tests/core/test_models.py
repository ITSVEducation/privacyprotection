from privacyprotection.core.models import (
    CATEGORY_LABELS, TOKEN_RE, Detection, Fragment,
    MappingEntry, MappingTable, RestoreResult,
)

def test_detection_defaults():
    d = Detection(text="山田太郎", category="PERSON", start=0, end=4, source="ner")
    assert d.enabled is True

def test_category_labels_cover_all_categories():
    assert CATEGORY_LABELS["PERSON"] == "人名"
    assert CATEGORY_LABELS["ORG"] == "組織"
    assert CATEGORY_LABELS["LOC"] == "地名"
    assert CATEGORY_LABELS["PHONE"] == "電話"
    assert CATEGORY_LABELS["EMAIL"] == "メール"
    assert CATEGORY_LABELS["ADDRESS"] == "住所"
    assert CATEGORY_LABELS["POSTAL"] == "番号"
    assert CATEGORY_LABELS["MYNUMBER"] == "番号"
    assert CATEGORY_LABELS["CREDITCARD"] == "番号"
    assert CATEGORY_LABELS["CUSTOM"] == "カスタム"

def test_token_re_matches_token_grammar():
    assert TOKEN_RE.fullmatch("【人名_1】")
    assert TOKEN_RE.fullmatch("【カスタム_42】")
    assert not TOKEN_RE.fullmatch("【人名1】")
    assert not TOKEN_RE.fullmatch("【未知種別_1】")

def test_mapping_table_lookup():
    t = MappingTable(entries=[MappingEntry("【人名_1】", "山田太郎", "PERSON", 5)])
    assert t.token_for("山田太郎") == "【人名_1】"
    assert t.original_for("【人名_1】") == "山田太郎"
    assert t.token_for("不明") is None
    assert t.original_for("【人名_9】") is None
