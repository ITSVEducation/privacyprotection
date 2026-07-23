from privacyprotection.core.masker import Masker
from privacyprotection.core.models import Detection


def det(text, cat, start):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source="ner")


def test_token_mode_replaces_and_builds_mapping():
    m = Masker(mode="token")
    frags = ["山田太郎です。山田太郎に連絡。"]
    ds = [[det("山田太郎", "PERSON", 0), det("山田太郎", "PERSON", 7)]]
    out, table = m.mask_fragments(frags, ds)
    assert out == ["【人名_1】です。【人名_1】に連絡。"]
    assert len(table.entries) == 1
    assert table.entries[0].count == 2

def test_token_numbering_per_label_and_cross_fragment_consistency():
    m = Masker(mode="token")
    out, table = m.mask_fragments(
        ["山田太郎と佐藤花子", "山田太郎", "株式会社A"],
        [[det("山田太郎", "PERSON", 0), det("佐藤花子", "PERSON", 5)],
         [det("山田太郎", "PERSON", 0)],
         [det("株式会社A", "ORG", 0)]],
    )
    assert out == ["【人名_1】と【人名_2】", "【人名_1】", "【組織_1】"]

def test_disabled_detection_not_masked():
    m = Masker(mode="token")
    d1 = det("山田太郎", "PERSON", 0)
    d1.enabled = False
    out, table = m.mask_fragments(["山田太郎"], [[d1]])
    assert out == ["山田太郎"]
    assert table.entries == []

def test_redact_mode_no_mapping():
    m = Masker(mode="redact")
    out, table = m.mask_fragments(["山田太郎です"], [[det("山田太郎", "PERSON", 0)]])
    assert out == ["●●●●です"]
    assert table.entries == []

def test_collision_scan_shifts_counter():
    m = Masker(mode="token")
    max_n = m.scan_existing_tokens(["既に【人名_3】がある文書"])
    assert max_n == 3
    out, _ = m.mask_fragments(["山田太郎"], [[det("山田太郎", "PERSON", 0)]])
    assert out == ["【人名_4】"]

def test_same_value_shared_across_number_labels():
    # POSTAL と MYNUMBER は同じ「番号」ラベルだがカウンタは共有される
    m = Masker(mode="token")
    out, _ = m.mask_fragments(
        ["100-0001 と 1234 5678 9012"],
        [[det("100-0001", "POSTAL", 0), det("1234 5678 9012", "MYNUMBER", 11)]],
    )
    assert out == ["【番号_1】 と 【番号_2】"]
