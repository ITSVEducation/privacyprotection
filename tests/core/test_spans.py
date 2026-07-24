"""core/spans.py の remaining_spans() 直接ユニットテスト。

patterns.py (PatternDetector) と detector.py (Detector) の両方が重複解決に
使う共有の区間差分ロジックを、ここで一箇所にまとめて直接検証する
（従来 test_patterns.py / test_detector.py それぞれに暗黙的・重複していた
このロジック自体のテストケースを統合する）。

test_patterns.py / test_detector.py 側の既存テスト（detect() 経由の統合テスト）
はそのまま残す。こちらは共有プリミティブ自体への直接的なユニットテスト。
"""
from privacyprotection.core.models import Detection
from privacyprotection.core.spans import remaining_spans, resolve_overlaps


def test_no_overlap_returns_span_unchanged():
    # taken と全く重ならない場合は span をそのまま1件返す。
    assert remaining_spans((5, 10), [(0, 3), (20, 25)]) == [(5, 10)]


def test_no_taken_at_all_returns_span_unchanged():
    assert remaining_spans((5, 10), []) == [(5, 10)]


def test_full_containment_discards_candidate_entirely():
    # taken が候補全体を覆っていれば空リスト（丸ごと破棄）。
    assert remaining_spans((5, 10), [(0, 15)]) == []


def test_exact_same_span_taken_discards_candidate_entirely():
    # taken が候補とちょうど同じ区間でも同様に丸ごと破棄。
    assert remaining_spans((5, 10), [(5, 10)]) == []


def test_prefix_survives_when_taken_overlaps_the_tail():
    # taken が候補の末尾側に重なる場合、前方(prefix)が生き残る。
    assert remaining_spans((0, 10), [(6, 15)]) == [(0, 6)]


def test_suffix_survives_when_taken_overlaps_the_head():
    # taken が候補の先頭側に重なる場合、後方(suffix)が生き残る。
    assert remaining_spans((5, 15), [(0, 9)]) == [(9, 15)]


def test_middle_claim_splits_candidate_into_two_segments():
    # taken が候補の中央にちょうど収まる場合、前後2区間に分割される。
    assert remaining_spans((0, 10), [(4, 6)]) == [(0, 4), (6, 10)]


def test_multiple_disjoint_taken_spans_split_into_several_segments():
    # 複数の taken 区間が候補の中に飛び飛びで存在する場合、
    # それぞれの隙間が非重複の残り区間として個別に生き残る。
    assert remaining_spans((0, 20), [(5, 8), (12, 15)]) == [
        (0, 5), (8, 12), (15, 20),
    ]


def test_unordered_taken_spans_are_handled_regardless_of_input_order():
    # taken の入力順に依存しない（内部でソートされる）ことを確認する。
    assert remaining_spans((0, 20), [(12, 15), (5, 8)]) == [
        (0, 5), (8, 12), (15, 20),
    ]


def test_adjacent_touching_taken_span_does_not_trim_candidate():
    # taken が候補の境界にちょうど接するだけ（重なりなし）の場合は
    # 半開区間 [start, end) の境界規約により重複とみなさず、候補全体が
    # そのまま生き残る（呼び出し側は text[start:end] スライスを前提に
    # しているため、この端点の扱いが正しいことは正確性上重要）。
    assert remaining_spans((5, 10), [(0, 5)]) == [(5, 10)]
    assert remaining_spans((5, 10), [(10, 15)]) == [(5, 10)]


def test_taken_span_partially_outside_candidate_is_clipped():
    # taken が候補の範囲をはみ出していても、はみ出た部分は無視され、
    # 候補の範囲内に収まる部分だけが重なりとして扱われる。
    assert remaining_spans((5, 10), [(0, 7)]) == [(7, 10)]
    assert remaining_spans((5, 10), [(8, 100)]) == [(5, 8)]


# --- resolve_overlaps() ---
#
# Task 18 で、Masker.mask_fragments() が「手動追加(source="manual")が既存の
# 自動検出と重なる」ケースを安全に解決するために追加した一般化マージ関数。
# Detector.detect() 内の重複解決アルゴリズム（設計書4.2）と同じロジックを
# 任意の priority マップに対して適用できるようにしたもの。

def _d(text, cat, start, source):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source=source)


def test_resolve_overlaps_no_overlap_keeps_both():
    text = "aaa bbb"
    candidates = [_d("aaa", "A", 0, "auto"), _d("bbb", "B", 4, "manual")]
    r = resolve_overlaps(candidates, text, {"manual": 0, "auto": 1})
    assert [(x.text, x.category, x.start, x.end) for x in r] == [
        ("aaa", "A", 0, 3), ("bbb", "B", 4, 7),
    ]


def test_resolve_overlaps_longer_span_wins_regardless_of_priority():
    text = "abcdefghij"
    shorter_higher_priority = _d("de", "B", 3, "manual")   # len2, priority 0 (best)
    longer_lower_priority = _d("cdefgh", "A", 2, "auto")   # len6, priority 1
    r = resolve_overlaps(
        [shorter_higher_priority, longer_lower_priority], text,
        {"manual": 0, "auto": 1})
    assert [(x.text, x.category) for x in r] == [("cdefgh", "A")]


def test_resolve_overlaps_same_length_uses_priority_as_tiebreak():
    text = "abcdefghij"
    auto = _d("de", "A", 3, "auto")
    manual = _d("de", "B", 3, "manual")
    r = resolve_overlaps([auto, manual], text, {"manual": 0, "auto": 1})
    assert [(x.text, x.category, x.source) for x in r] == [("de", "B", "manual")]


def test_resolve_overlaps_partial_overlap_trims_the_loser_not_discard():
    text = "0123456789AB"
    auto = _d("56789", "PHONE", 5, "auto")     # (5,10)
    manual = _d("789AB", "PERSON", 7, "manual")  # (7,12), same length as auto
    r = resolve_overlaps([auto, manual], text, {"manual": 0, "auto": 1})
    # manual は priority が高い(0<1)ので (7,12) を丸ごと維持。auto は
    # 重ならない残り部分 (5,7)="56" だけがトリムされて生き残る。
    assert [(x.text, x.category, x.start, x.end) for x in r] == [
        ("56", "PHONE", 5, 7), ("789AB", "PERSON", 7, 12),
    ]
    for x in r:
        assert text[x.start:x.end] == x.text


def test_resolve_overlaps_unknown_source_treated_as_lowest_priority():
    # priority マップに存在しない source は最低優先度(末尾)として扱われる
    # ため、同じ長さなら既知の source を持つ候補に敗れる。
    text = "abcdefghij"
    known = _d("de", "A", 3, "manual")
    unknown = _d("de", "B", 3, "some_future_source")
    r = resolve_overlaps([known, unknown], text, {"manual": 0})
    assert [(x.category, x.source) for x in r] == [("A", "manual")]
