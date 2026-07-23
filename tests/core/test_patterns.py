from privacyprotection.core.patterns import PatternDetector

det = PatternDetector()

def cats(text):
    return [(d.text, d.category) for d in det.detect(text)]

def test_phone_hyphenated_and_plain():
    assert ("03-1234-5678", "PHONE") in cats("電話は03-1234-5678です")
    assert ("09012345678", "PHONE") in cats("携帯09012345678まで")

def test_email():
    assert ("yamada@example.co.jp", "EMAIL") in cats("送付先: yamada@example.co.jp")

def test_postal_code():
    assert ("〒100-0001", "POSTAL") in cats("〒100-0001 千代田区")
    assert ("100-0001", "POSTAL") in cats("郵便番号は100-0001")

def test_mynumber_12_digits():
    assert ("1234 5678 9012", "MYNUMBER") in cats("マイナンバー: 1234 5678 9012")

def test_creditcard_16_digits():
    assert ("1234-5678-9012-3456", "CREDITCARD") in cats("カード番号 1234-5678-9012-3456")

def test_address():
    found = cats("住所は東京都千代田区丸の内1-1-1です")
    assert any(c == "ADDRESS" and t.startswith("東京都") for t, c in found)

def test_no_false_positive_on_plain_text():
    assert cats("これは普通の文章です。") == []

def test_digits_inside_longer_number_not_matched():
    # 20桁の連番はクレカ(16桁)として部分マッチしない
    assert all(c != "CREDITCARD" for _, c in cats("12345678901234567890"))

def test_leading_glued_digit_blocks_creditcard_false_match():
    # CREDITCARDの境界ガードは (?<!\d) の1文字後読みのみで十分: 「01234-5678-9012-3456」
    # で「1234-5678-9012-3456」(index1開始)が誤って16桁カードとして検出されないのは、
    # 直前の文字(index0の"0")が数字であるため。区切り文字越しに2文字分先読み・後読み
    # する (?<!\d[- ]) / (?![- ]\d) のような拡張ガードは不要（一度導入されたが、
    # 正規の16桁カード自体を検出できなくするregressionだとレビューで判明し差し戻し
    # 済み。詳細は patterns.py 冒頭のコメント、および
    # test_creditcard_not_over_blocked_by_adjacent_digit_across_separator を参照）。
    assert all(c != "CREDITCARD" for _, c in cats("01234-5678-9012-3456"))


def test_creditcard_not_over_blocked_by_adjacent_digit_across_separator():
    # regression再発防止: 区切り文字を挟んで隣接する無関係な数字（別文脈の数字、
    # 箇条書き番号など）があっても、正規に区切られた16桁カード番号自体は
    # 引き続き検出されなければならない。1文字ガードから2文字ガードへ広げた
    # commit 756e712 は、これらすべてを検出漏れにする regression だった。
    for text in (
        "5 1234-5678-9012-3456",
        "1234-5678-9012-3456 7",
        "5-1234-5678-9012-3456",
    ):
        results = det.detect(text)
        assert ("1234-5678-9012-3456", "CREDITCARD") in {
            (d.text, d.category) for d in results
        }
        for d in results:
            assert text[d.start:d.end] == d.text


def test_mynumber_not_over_blocked_by_adjacent_digit_across_separator():
    # 上と同じ regression の MYNUMBER (12桁) 版。
    for text in (
        "5 1234 5678 9012",
        "1234 5678 9012 7",
    ):
        results = det.detect(text)
        assert ("1234 5678 9012", "MYNUMBER") in {
            (d.text, d.category) for d in results
        }
        for d in results:
            assert text[d.start:d.end] == d.text

def test_overlapping_candidate_is_trimmed_not_discarded():
    # 再現ケース: ADDRESSの生マッチ範囲(0,26)はPHONEの範囲(14,26)と部分的に重なる
    # （包含関係ではない）。修正前はADDRESS候補が丸ごと破棄され住所が未検出になって
    # いたが、修正後は重ならない残り部分（前方）がADDRESSとして生き残り、PHONEも
    # 両方とも検出される。
    text = "東京都千代田区1-1-1電話03-1234-5678"
    results = det.detect(text)
    found = {(d.text, d.category) for d in results}
    assert ("03-1234-5678", "PHONE") in found

    addresses = [d for d in results if d.category == "ADDRESS"]
    assert len(addresses) == 1
    assert addresses[0].text.startswith("東京都千代田区")

    # トリミングされた検出のtextは、原文の[start:end]と完全に一致しなければならない
    # （ここがずれるとマスク処理時に置換位置がずれ、原文が壊れる）。
    for d in results:
        assert text[d.start:d.end] == d.text

def test_overlap_in_middle_splits_candidate_into_two_segments():
    # PHONEの範囲がADDRESS候補の中央付近を占有すると、ADDRESSは前後2つの
    # 断片に分割されて残る（重複解決が単純な前方/後方トリムだけでなく、
    # 中抜きにも対応していることの確認）。
    text = "東京都千代田区1-1-1電話03-1234-5678番地"
    results = det.detect(text)
    assert ("03-1234-5678", "PHONE") in {(d.text, d.category) for d in results}

    addresses = [d for d in results if d.category == "ADDRESS"]
    assert len(addresses) == 2
    assert addresses[0].text == "東京都千代田区1-1-1電話"
    assert addresses[1].text == "番地"
    for d in results:
        assert text[d.start:d.end] == d.text

def test_full_overlap_discards_candidate_entirely():
    # 旧バージョンの本テストは「カード番号 1234-5678-9012-3456」でMYNUMBER検出が
    # 無いことを確認していたが、それは当時の(誤って広すぎた)境界ガードのせいで
    # MYNUMBERの生マッチがそもそも成立していなかった（＝重複解決の破棄ロジックを
    # 一切通っていなかった）ことが原因だと判明した（レビューでの重要な指摘）。
    # ここでは境界ガードの状態に依存しない別カテゴリの組み合わせに置き換え、
    # 破棄(discard)ロジックを確実に経由させる。
    #
    # 「電話090-1234-5678です」: PHONEが `0\d{1,4}-\d{1,4}-\d{3,4}` の代替
    # パターンで先に「090-1234-5678」を確定させる。その後、POSTALの素の数字
    # パターン `\d{3}-\d{4}` が独立に「090-1234」に生マッチするが、この範囲は
    # PHONEが確定済みの範囲に完全に包含されるため、非重複の残り部分が存在せず
    # 破棄される（実際に生の re.finditer で両方のマッチが独立に成立すること、
    # かつ detect() の結果からPOSTALだけが消えることを確認済み）。
    text = "電話090-1234-5678です"
    results = det.detect(text)
    found = {(d.text, d.category) for d in results}
    assert ("090-1234-5678", "PHONE") in found
    assert all(d.category != "POSTAL" for d in results)

    for d in results:
        assert text[d.start:d.end] == d.text

def test_results_sorted_by_start():
    r = det.detect("a@b.jp と 03-1234-5678")
    assert [d.start for d in r] == sorted(d.start for d in r)
