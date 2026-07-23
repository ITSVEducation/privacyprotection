from privacyprotection.core.models import MappingEntry, MappingTable
from privacyprotection.core.restorer import Restorer

TABLE = MappingTable(entries=[
    MappingEntry("【人名_1】", "山田太郎", "PERSON", 2),
    MappingEntry("【人名_2】", "佐藤花子", "PERSON", 1),
])

def test_restores_known_tokens():
    r = Restorer(TABLE).restore("【人名_1】と【人名_2】が【人名_1】に")
    assert r.text == "山田太郎と佐藤花子が山田太郎に"
    assert r.unknown_tokens == []

def test_unknown_token_left_and_reported():
    r = Restorer(TABLE).restore("【人名_1】と【人名_9】")
    assert r.text == "山田太郎と【人名_9】"
    assert r.unknown_tokens == ["【人名_9】"]

def test_altered_token_not_fuzzy_matched():
    # AIが改変したトークン（区切り欠落）は復元されない
    r = Restorer(TABLE).restore("【人名1】のままです")
    assert r.text == "【人名1】のままです"
    assert r.unknown_tokens == []  # トークン文法に一致しないので unknown 扱いもしない

def test_mask_restore_roundtrip():
    from privacyprotection.core.masker import Masker
    from privacyprotection.core.models import Detection
    original = "担当は山田太郎（電話03-1234-5678）です。"
    m = Masker(mode="token")
    ds = [Detection("山田太郎", "PERSON", 3, 7, "ner"),
          Detection("03-1234-5678", "PHONE", 10, 22, "pattern")]
    masked, table = m.mask_fragments([original], [ds])
    restored = Restorer(table).restore(masked[0])
    assert restored.text == original
