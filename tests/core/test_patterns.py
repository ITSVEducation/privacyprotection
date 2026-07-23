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

def test_delimited_run_one_digit_longer_not_matched_as_creditcard_or_mynumber():
    # 上のテストは区切り文字(-や空白)が一切ない20桁連続なので、CREDITCARD/MYNUMBER
    # が要求する区切り文字自体が存在せず、(?<!\d)/(?!\d) の境界ガードは実質検証
    # されない（ガードを外しても同じ結果になる）。区切りを挟みつつ正規の16桁/12桁
    # より1桁多い並びで、境界ガードが誤検出を正しく防ぐことを検証する。
    # ("1234-5678-9012-3456-7" は正規の16桁カードの後ろにさらに1桁が区切り付きで
    #  続く形、"01234-5678-9012-3456" は先頭の4桁グループに1桁がくっついた形。)
    assert cats("1234-5678-9012-3456-7") == []
    assert all(c not in ("CREDITCARD", "MYNUMBER") for _, c in cats("01234-5678-9012-3456"))

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
    # MYNUMBERの生マッチ「5678-9012-3456」はCREDITCARDが先に確定した範囲
    # (6, 25)に完全に包含されるため、非重複の残り部分が存在せず、
    # MYNUMBERの検出は一切結果に現れない（discard）。
    text = "カード番号 1234-5678-9012-3456"
    results = det.detect(text)
    assert ("1234-5678-9012-3456", "CREDITCARD") in {(d.text, d.category) for d in results}
    assert all(d.category != "MYNUMBER" for d in results)

def test_results_sorted_by_start():
    r = det.detect("a@b.jp と 03-1234-5678")
    assert [d.start for d in r] == sorted(d.start for d in r)
