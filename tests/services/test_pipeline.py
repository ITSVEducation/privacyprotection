import pytest
from privacyprotection.core.detector import Detector
from privacyprotection.core.dictionary import DictionaryDetector
from privacyprotection.core.mapping_io import read_mapping
from privacyprotection.core.models import TOKEN_RE
from privacyprotection.core.patterns import PatternDetector
from privacyprotection.handlers.text_handler import TextHandler
from privacyprotection.services.pipeline import Pipeline


def make_pipeline(mode="token"):
    det = Detector([PatternDetector(),
                    DictionaryDetector({"山田太郎": "PERSON", "佐藤花子": "PERSON"})])
    return Pipeline(detector=det, mode=mode)


def test_mask_file_outputs_masked_and_mapping(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎 03-1234-5678", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    out, report = pl.mask_file(src, frags, dets)
    assert out.name == "memo_masked.txt"
    assert out.read_text(encoding="utf-8") == "【人名_1】 【電話_1】"
    assert src.read_text(encoding="utf-8") == "山田太郎 03-1234-5678"  # 元ファイル不変
    mapping = read_mapping(tmp_path / "memo_masked.txt.pmap.csv")
    assert len(mapping.entries) == 2
    assert report.category_counts == {"PERSON": 1, "PHONE": 1}


def test_existing_output_gets_numbered_suffix(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    (tmp_path / "memo_masked.txt").write_text("既存", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    out, _ = pl.mask_file(src, frags, dets)
    assert out.name == "memo_masked(2).txt"
    assert (tmp_path / "memo_masked.txt").read_text(encoding="utf-8") == "既存"


def test_redact_mode_produces_no_mapping(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline(mode="redact")
    frags, dets = pl.analyze_file(src)
    out, _ = pl.mask_file(src, frags, dets)
    assert out.read_text(encoding="utf-8") == "●●●●"
    assert not (tmp_path / "memo_masked.txt.pmap.csv").exists()


def test_disabled_detection_respected(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎と佐藤花子", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    for d in dets[0]:
        if d.text == "佐藤花子":
            d.enabled = False
    out, report = pl.mask_file(src, frags, dets)
    assert out.read_text(encoding="utf-8") == "【人名_1】と佐藤花子"


def test_mask_folder_shared_tokens_and_report(tmp_path):
    (tmp_path / "a.txt").write_text("山田太郎", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("山田太郎の件", encoding="utf-8")
    (tmp_path / "c.pdf").write_bytes(b"x")
    pl = make_pipeline()
    batch, mapping_path = pl.mask_folder(tmp_path)
    # 同一人物は全ファイルで同一トークン
    assert (tmp_path / "a_masked.txt").read_text(encoding="utf-8") == "【人名_1】"
    assert (tmp_path / "sub" / "b_masked.txt").read_text(encoding="utf-8") == "【人名_1】の件"
    assert mapping_path == tmp_path / "_folder.pmap.csv"
    assert (tmp_path / "_report.txt").exists()
    assert any(p.name == "c.pdf" for p, _ in batch.skipped)


def test_mask_folder_updates_mapping_before_each_masked_file(tmp_path, monkeypatch):
    """グローバル制約「対応表はマスク済ファイルより先に確定する」は、フォルダ
    一括処理でも各ファイル単位で保たれなければならない(バッチ全体の最後に
    1回だけ書くのでは不十分:途中で処理が中断されると、その時点までに書かれた
    マスク済ファイルのトークンが対応表に一切載っていない状態になり得る)。

    ここでは、各ファイルのマスク済み出力が実際にディスクへ書かれる直前の
    時点で、_folder.pmap.csv が既にそのファイルの新規トークンを含んだ状態に
    更新されていることを検証する。
    """
    (tmp_path / "a.txt").write_text("山田太郎", encoding="utf-8")
    (tmp_path / "b.txt").write_text("佐藤花子", encoding="utf-8")

    original_write = TextHandler.write_fragments
    checked: list[tuple[str, bool]] = []

    def spy_write(self, src, dst, masked):
        mapping_file = tmp_path / "_folder.pmap.csv"
        content = mapping_file.read_text(encoding="utf-8-sig") if mapping_file.exists() else ""
        for frag in masked:
            for tok in TOKEN_RE.findall(frag.text):
                checked.append((tok, tok in content))
        return original_write(self, src, dst, masked)

    monkeypatch.setattr(TextHandler, "write_fragments", spy_write)

    pl = make_pipeline()
    pl.mask_folder(tmp_path)

    assert checked  # スパイが実際に何かを検証したことを確認（誤って空なら無意味）
    assert all(present for _, present in checked), checked


def test_restore_file_roundtrip(tmp_path):
    src = tmp_path / "memo.txt"
    original = "山田太郎 03-1234-5678 佐藤花子"
    src.write_text(original, encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    restored_path, result = pl.restore_file(masked_path)  # pmap自動検出
    assert restored_path.read_text(encoding="utf-8") == original
    assert result.unknown_tokens == []


def test_restore_text_with_unknown_token(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    r = pl.restore_text("【人名_1】と【人名_9】",
                        masked_path.parent / (masked_path.name + ".pmap.csv"))
    assert r.text == "山田太郎と【人名_9】"
    assert r.unknown_tokens == ["【人名_9】"]


def test_broken_file_reported_not_raised(tmp_path):
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"not a zip")
    pl = make_pipeline()
    batch, _ = pl.mask_folder(tmp_path)
    assert len(batch.files) == 1
    assert batch.files[0].error is not None


def test_restore_file_numbered_masked_suffix(tmp_path):
    """マスク済み出力が連番付き（memo_masked(2).txt）の場合でも、その連番が
    復元ファイル名の語幹に紛れ込まない（stem.replace()によるバグの回帰テスト）。"""
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    # 1つ目の "memo_masked.txt" を先に存在させておき、2つ目を連番にする
    (tmp_path / "memo_masked.txt").write_text("既存", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    assert masked_path.name == "memo_masked(2).txt"

    restored_path, result = pl.restore_file(masked_path)
    assert restored_path.name == "memo_restored.txt"
    assert restored_path.read_text(encoding="utf-8") == "山田太郎"
    assert result.unknown_tokens == []


def test_restore_file_preserves_unrelated_masked_substring(tmp_path):
    """ファイル名にたまたま '_masked' を含む(本アプリ由来でない)場合、
    その部分文字列を誤って削ってはならない(サフィックス限定の除去であること)。"""
    src = tmp_path / "already_masked_by_someone_else.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    assert masked_path.name == "already_masked_by_someone_else_masked.txt"

    restored_path, _ = pl.restore_file(masked_path)
    # 語幹全体から "_masked" というサフィックスだけを取り除く。
    # 語中の "_masked" を盲目的に削ると
    # "already_by_someone_else_restored.txt" のような誤ったファイル名になる。
    assert restored_path.name == "already_masked_by_someone_else_restored.txt"
