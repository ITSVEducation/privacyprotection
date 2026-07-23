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

def test_results_sorted_by_start():
    r = det.detect("a@b.jp と 03-1234-5678")
    assert [d.start for d in r] == sorted(d.start for d in r)
