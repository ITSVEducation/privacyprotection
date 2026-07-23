from privacyprotection.handlers.folder_walker import walk
from privacyprotection.handlers.registry import get_handler
from privacyprotection.handlers.text_handler import TextHandler
from privacyprotection.handlers.xlsx_handler import XlsxHandler


def test_registry_resolves_by_extension(tmp_path):
    assert isinstance(get_handler(tmp_path / "a.txt"), TextHandler)
    assert isinstance(get_handler(tmp_path / "A.XLSX"), XlsxHandler)  # 大文字も可
    assert get_handler(tmp_path / "a.pdf") is None


def test_walk_collects_supported_and_skips(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub" / "b.md").write_text("x")
    (tmp_path / "c.pdf").write_bytes(b"x")            # 未対応
    (tmp_path / "~$lock.docx").write_bytes(b"x")      # Officeロックファイル
    (tmp_path / "d_masked.txt").write_text("x")       # 既存出力
    (tmp_path / "e.pmap.csv").write_text("x")         # 対応表
    (tmp_path / "_report.txt").write_text("x")        # レポート
    r = walk(tmp_path)
    names = sorted(p.name for p in r.supported)
    assert names == ["a.txt", "b.md"]
    skipped_names = {p.name for p, _ in r.skipped}
    assert "c.pdf" in skipped_names
    assert "~$lock.docx" in skipped_names
