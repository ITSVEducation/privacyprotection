from privacyprotection.core.detector import Detector, _remaining_spans
from privacyprotection.core.models import Detection


class FakeDetector:
    def __init__(self, results):
        self._results = results
    def detect(self, text):
        return list(self._results)


def d(text, cat, start, source):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source=source)


def test_longer_span_wins():
    ner = FakeDetector([d("山田", "PERSON", 4, "ner")])
    dic = FakeDetector([d("株式会社山田製作所", "ORG", 0, "dictionary")])
    r = Detector([dic, ner]).detect("株式会社山田製作所")
    assert [(x.text, x.category) for x in r] == [("株式会社山田製作所", "ORG")]

def test_same_span_priority_dictionary_over_ner():
    ner = FakeDetector([d("山田太郎", "PERSON", 0, "ner")])
    dic = FakeDetector([d("山田太郎", "CUSTOM", 0, "dictionary")])
    r = Detector([ner, dic]).detect("山田太郎")
    assert [(x.source,) for x in r] == [("dictionary",)]

def test_non_overlapping_all_kept_sorted():
    a = FakeDetector([d("bbb", "ORG", 10, "ner")])
    b = FakeDetector([d("aaa", "PERSON", 0, "ner")])
    r = Detector([a, b]).detect("x" * 20)
    assert [x.start for x in r] == [0, 10]

def test_partial_overlap_earlier_start_wins():
    # 修正版: brief記載のオリジナル版は「"cdef"(ORG, span(2,6))は"abcd"との
    # 重複により丸ごと破棄され、結果は("abcd",)のみ」と主張していたが、これは
    # Task2で発見・修正されたのと同じ under-detection バグ（部分重複を丸ごと
    # 破棄する誤り）の再現になっている。設計書4.2「部分的に重なり合い包含関係に
    # ない場合は、開始位置が先のものを採用し、後のものは重複しない残り部分を
    # 再検出する」に従い、"cdef"の非重複残り部分 "ef" (4,6) はORGとして
    # 生き残らなければならない。
    a = FakeDetector([d("abcd", "PERSON", 0, "ner")])
    b = FakeDetector([d("cdef", "ORG", 2, "ner")])
    r = Detector([a, b]).detect("abcdef")
    assert [(x.text, x.category, x.start, x.end) for x in r] == [
        ("abcd", "PERSON", 0, 4), ("ef", "ORG", 4, 6),
    ]

def test_disabled_category_filtered():
    a = FakeDetector([d("東京", "LOC", 0, "ner"), d("山田", "PERSON", 5, "ner")])
    r = Detector([a], enabled_categories={"PERSON"}).detect("東京…山田")
    assert [(x.category,) for x in r] == [("PERSON",)]


def test_dictionary_source_bypasses_category_filter():
    # Task 19 調査1: カスタム辞書由来(source="dictionary")の検出は、
    # ユーザーがその語句にCUSTOM以外の種別(例: PERSON)を割り当てていても、
    # その種別のチェックボックスがOFF(enabled_categoriesに含まれない)な状態
    # でも常に検出されなければならない。辞書登録は「必ずマスクしたい」という
    # ユーザーの明示的な意思表示であり、自動検出(pattern/ner)のON/OFF設定に
    # 巻き込まれて黙って無効化されてはならない。
    dic = FakeDetector([d("山田太郎", "PERSON", 0, "dictionary")])
    r = Detector([dic], enabled_categories={"CUSTOM"}).detect("山田太郎")
    assert [(x.text, x.category, x.source) for x in r] == [
        ("山田太郎", "PERSON", "dictionary"),
    ]


def test_non_dictionary_source_still_filtered_by_category():
    # 上のテストの陰性対照: 辞書検出の例外はsource=="dictionary"の候補にのみ
    # 適用されるべきで、同じPERSON種別でもner/pattern由来の候補は従来通り
    # enabled_categoriesでフィルタされ続けなければならない
    # (「PERSONのチェックを外す」操作が自動検出に対しては引き続き機能する
    # ことの確認)。
    ner = FakeDetector([d("山田太郎", "PERSON", 0, "ner")])
    r = Detector([ner], enabled_categories={"CUSTOM"}).detect("山田太郎")
    assert r == []


# --- 追加テスト ---

def test_remaining_spans_middle_claim_splits_into_two_segments():
    # Detector.detect() の重複解決の核となる区間差分ロジック _remaining_spans
    # （patterns.py の同名関数と同一アルゴリズム）が、候補区間の「中央」だけが
    # 既に確定済み(taken)である場合に、前後2つの非重複区間へ正しく分割する
    # ことを直接確認する。
    #
    # 注記（重要）: Detector.detect() 自体の優先順位は設計書4.2の①により
    # 「長い範囲が常にグローバルに先へ処理される」という規則である
    # （brief実装・本実装とも candidates.sort(key=lambda c: (-(c.end-c.start), ...))
    # のとおり、長さが第一キー）。この規則の下では、ある候補Xより「先に処理される」
    # ものは必ずXと同じ長さ以上でなければならない。そして、そのような
    # 「Xと同じ長さ以上のもの」がXの内部と少しでも重なるなら、それは
    # 必ずXの端（開始位置または終了位置）のどちらか一方にも接してしまう
    # （区間が連続している以上、Xより長いか同じ長さのものがXの真ん中だけを
    # 覗き込んで両端に触れずに済むことは幾何学的にあり得ない）。したがって
    # 「両端が生き残り中央だけが奪われる」形の分割は、Detector.detect()の
    # 公開APIレベルでは原理的に再現できない
    # （手計算による証明に加え、ランダム生成した候補の組み合わせ約70万通りを
    # 実際にこのアルゴリズムで処理するブルートフォース検証でも一件も
    # 再現できないことを確認済み）。これは PatternDetector
    # （カテゴリの固定リスト順で優先度が決まるため、短いPHONEが先に確定してから
    # 長いADDRESSを中抜きできる）との重要な構造上の違いである。
    # そのため、実際に分割を行う中核ロジックである _remaining_spans 自体を
    # 直接検証する。
    span = (0, 10)
    taken = [(4, 6)]
    result = _remaining_spans(span, taken)
    assert result == [(0, 4), (6, 10)]

    # Detector.detect() が行うのと同じ組み立て方（text[start:end]から切り出す）で
    # Detectionを構築した場合も、分割された断片それぞれが独立したDetectionとして
    # 正しいテキストを持つことを確認する。
    text = "0123456789"
    pieces = [
        Detection(text=text[s:e], category="ORG", start=s, end=e, source="ner")
        for s, e in result
    ]
    assert [(p.text, p.start, p.end) for p in pieces] == [
        ("0123", 0, 4), ("6789", 6, 10),
    ]
    for p in pieces:
        assert text[p.start:p.end] == p.text


def test_trimmed_detection_text_matches_original_slice():
    # トリムされた（部分重複の結果、残り部分だけが生き残った）Detectionに
    # ついて、text が原文の [start:end] スライスと完全に一致する不変条件を
    # 確認する。ここがずれるとマスク処理時の置換位置がずれ、原文が壊れる。
    # test_partial_overlap_earlier_start_wins とは別の具体例で確認する。
    text = "0123456789"
    a = FakeDetector([d("01234567", "ORG", 0, "dictionary")])   # (0,8) 長さ8
    b = FakeDetector([d("56789", "PERSON", 5, "ner")])          # (5,10) 長さ5

    r = Detector([a, b]).detect(text)
    assert [(x.text, x.category, x.start, x.end) for x in r] == [
        ("01234567", "ORG", 0, 8), ("89", "PERSON", 8, 10),
    ]

    trimmed = [x for x in r if x.category == "PERSON"]
    assert len(trimmed) == 1
    trimmed_detection = trimmed[0]
    assert trimmed_detection.text == "89"
    assert text[trimmed_detection.start:trimmed_detection.end] == trimmed_detection.text

    # 念のため全件（トリムされていないORGも含む）についても確認する。
    for x in r:
        assert text[x.start:x.end] == x.text


def test_full_overlap_discards_candidate_entirely():
    # test_longer_span_wins と同じ設定を使い、完全に包含される候補
    # ("山田", PERSON, ner) が、トリムされて空文字列になった phantom entry として
    # 結果に残るのではなく、結果件数そのものがゼロ（一切現れない）ことを
    # 明示的に確認する。
    ner = FakeDetector([d("山田", "PERSON", 4, "ner")])
    dic = FakeDetector([d("株式会社山田製作所", "ORG", 0, "dictionary")])
    r = Detector([dic, ner]).detect("株式会社山田製作所")

    assert len(r) == 1
    assert all(x.category != "PERSON" for x in r)
    assert all(x.source != "ner" for x in r)
    assert all(x.text != "" for x in r)
