"""gui/preview_dialog.py の build_display_map() 直接ユニットテスト。

GUI層は原則として手動確認だが、この関数は QApplication を必要としない純粋
関数であり、かつプライバシー上重要な不変条件（ユーザーがドラッグで選んだ語と
実際にマスクされる範囲が一致すること）を支えているためユニットテストする。

背景: QTextDocument.setPlainText() は "\r\n" と単独 "\r" をそれぞれ1つの
段落区切り（1文字ぶんの位置）に畳むため、Python文字列のオフセットと表示位置が
ずれる。Windowsのクリップボードは CRLF を含むことが多く、変換せずに扱うと
ハイライトが無関係な箇所に付き、ドラッグ追加も別の範囲を登録してしまう。
"""
from privacyprotection.gui.preview_dialog import build_display_map


def test_no_newlines_is_identity():
    text = "山田太郎 03-1234-5678"
    disp, py2disp, disp2py = build_display_map(text)
    assert disp == text
    assert py2disp == list(range(len(text) + 1))
    assert disp2py == list(range(len(text) + 1))


def test_lf_only_is_identity():
    text = "1行目\n2行目"
    disp, py2disp, disp2py = build_display_map(text)
    assert disp == text
    assert py2disp == list(range(len(text) + 1))


def test_crlf_collapses_to_single_display_position():
    text = "AB\r\nCD"
    disp, py2disp, disp2py = build_display_map(text)
    assert disp == "AB\nCD"
    # 表示は1文字ぶん短い
    assert len(disp) == len(text) - 1
    # CRLF 以降の Python オフセットは表示上1つ手前へ寄る
    assert py2disp[text.index("C")] == disp.index("C")
    assert py2disp[len(text)] == len(disp)


def test_lone_cr_also_collapses():
    text = "AB\rCD"
    disp, py2disp, _ = build_display_map(text)
    assert disp == "AB\nCD"
    assert py2disp[text.index("C")] == disp.index("C")


def test_roundtrip_maps_are_consistent_for_every_character():
    """すべての文字について disp2py(py2disp(i)) が元の文字を指すこと。
    これが崩れるとハイライト位置・ドラッグ追加範囲がずれる。"""
    text = "名前:山田太郎\r\n電話:03-1234-5678\r\n住所:東京都\r\n"
    disp, py2disp, disp2py = build_display_map(text)
    for i, ch in enumerate(text):
        if ch in "\r\n":
            continue
        assert disp[py2disp[i]] == ch, (i, ch)
        assert disp2py[py2disp[i]] == i, (i, ch)


def test_detection_span_maps_to_same_substring():
    """CRLF を含むテキストでも、検出スパンを表示位置へ変換した範囲が
    元と同じ文字列を指すこと（着色位置ずれの回帰テスト）。"""
    text = "1行目\r\n2行目\r\n担当は山田太郎です"
    start = text.index("山田太郎")
    end = start + 4
    disp, py2disp, _ = build_display_map(text)
    assert disp[py2disp[start]:py2disp[end]] == "山田太郎"
