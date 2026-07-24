from privacyprotection.core.masker import Masker
from privacyprotection.core.models import Detection


def det(text, cat, start):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source="ner")


def manual(text, cat, start):
    # プレビュー画面の手動追加(_add_manual)を模す。source="manual" は
    # Detector.detect() の重複解決を経由せずに直接リストへ追記される
    # （Task 18: PreviewDialog._add_manual）ため、Masker側で安全に
    # 解決できることを検証する必要がある。
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source="manual")


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


# --- Task 18: 手動追加が既存の自動検出と重なる場合の安全性 ---
#
# PreviewDialog._add_manual() は Detector.detect() の重複解決を経由せず、
# source="manual" の Detection を直接リストへ追記する。mask_fragments() が
# 「渡された有効(enabled)なDetection群は互いに重ならない」前提のまま
# 開始位置降順で置換していた旧実装では、手動追加が既存の自動検出と重なると
# 文字位置がずれ、原文破壊・検出漏れ（PIIの未マスク残存）が実際に起きて
# いたことを、このタスクの調査（スクリプトによる実行確認）で確認済み。
# 以下は、その修正（Masker.mask_fragments 内での resolve_overlaps 適用）が
# 実際に安全な結果を返すことを固定するテスト。

def test_manual_partial_overlap_with_auto_detection_is_trimmed_not_corrupted():
    # 自動検出 "56789"(PHONE, 5-10) と、手動追加 "789AB"(PERSON, 7-12) が
    # 部分的に重なる（どちらも部分包含関係にはない）。同じ長さ(5文字)なので
    # manual が優先され(_ACTIVE_SOURCE_PRIORITY)、自動検出側は非重複の
    # 残り部分 "56" だけがPHONEとして生き残る（丸ごと破棄ではない）。
    text = "0123456789AB"
    auto = det("56789", "PHONE", 5)
    man = manual("789AB", "PERSON", 7)
    m = Masker(mode="token")
    out, table = m.mask_fragments([text], [[auto, man]])
    # 文字が失われたり二重にマスクされたりせず、全区間が過不足なく
    # 説明できることを確認する（"01234" + PHONE分 + PERSON分 で原文の
    # 全12文字を過不足なく再構成できる）。
    assert out == ["01234【電話_1】【人名_1】"]
    originals = {e.category: e.original for e in table.entries}
    assert originals == {"PHONE": "56", "PERSON": "789AB"}


def test_manual_fully_inside_auto_detection_is_discarded_not_double_masked():
    # 自動検出 "cdefgh"(PERSON, 2-8) の内側を、ユーザーが手動で
    # "de"(EMAIL, 3-5) として選び直した場合。自動検出の方が長い(6>2)ため
    # 自動検出がそのまま採用され、完全に包含される手動追加は（設計書4.2の
    # 「完全に覆われる場合のみ丸ごと破棄」規則どおり）破棄される。
    # 重要なのは、"cdefgh" 全体が過不足なく1回だけマスクされ、"de" の
    # 部分だけが未マスクのまま残ったり、逆に二重にマスクされたりしないこと。
    text = "abcdefghij"
    auto = det("cdefgh", "PERSON", 2)
    man = manual("de", "EMAIL", 3)
    m = Masker(mode="token")
    out, table = m.mask_fragments([text], [[auto, man]])
    assert out == ["ab【人名_1】ij"]
    assert [e.category for e in table.entries] == ["PERSON"]


def test_manual_fully_inside_auto_detection_redact_mode_no_leftover_chars():
    # 上と同じ重なりを不可逆(redact)モードで確認する。中間の文字("gh"など)が
    # 置換されずリテラルのまま残る（＝検出漏れ）ことがないことを確認する。
    text = "abcdefghij"
    auto = det("cdefgh", "PERSON", 2)
    man = manual("de", "EMAIL", 3)
    m = Masker(mode="redact")
    out, table = m.mask_fragments([text], [[auto, man]])
    assert out == ["ab●●●●ij"]
    assert table.entries == []


def test_manual_same_span_as_auto_detection_wins_reclassification():
    # ユーザーが自動検出とまったく同じ範囲を選び、別カテゴリとして手動追加
    # した場合（＝再分類したい場合）。設計書4.3「プレビューでの手動操作＞
    # 検出器」の優先順位どおり、手動追加のカテゴリが採用され、自動検出側は
    # 破棄される（同じ範囲が二重にマスクされることはない）。
    text = "abcdefghij"
    auto = det("de", "EMAIL", 3)
    man = manual("de", "PERSON", 3)
    m = Masker(mode="token")
    out, table = m.mask_fragments([text], [[auto, man]])
    assert out == ["abc【人名_1】fghij"]
    assert [(e.category, e.original) for e in table.entries] == [("PERSON", "de")]


def test_manual_detection_containing_auto_detection_wins_by_length():
    # 逆に、手動追加の方が範囲が広い場合（自動検出を包含する）は、範囲が
    # 長い方を優先する規則(設計書4.2-1)により手動側が採用され、内側の
    # 自動検出は破棄される。
    text = "abcdefghij"
    auto = det("de", "EMAIL", 3)
    man = manual("cdefgh", "PERSON", 2)
    m = Masker(mode="token")
    out, table = m.mask_fragments([text], [[auto, man]])
    assert out == ["ab【人名_1】ij"]
    assert [(e.category, e.original) for e in table.entries] == [("PERSON", "cdefgh")]


def test_already_non_overlapping_detections_unaffected_by_overlap_resolution():
    # 重ならない通常のケース（手動追加が無関係の位置にある場合）では、
    # 重複解決の追加が既存の挙動に影響しないことを確認する回帰テスト。
    text = "山田太郎と佐藤花子"
    auto = det("山田太郎", "PERSON", 0)
    man = manual("佐藤花子", "PERSON", 5)
    m = Masker(mode="token")
    out, table = m.mask_fragments([text], [[auto, man]])
    assert out == ["【人名_1】と【人名_2】"]
    assert [e.original for e in table.entries] == ["山田太郎", "佐藤花子"]
