import ctypes
import subprocess

from privacyprotection.handlers.folder_walker import walk, walk_masked
from privacyprotection.handlers.registry import get_handler
from privacyprotection.handlers.text_handler import TextHandler
from privacyprotection.handlers.xlsx_handler import XlsxHandler

_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4


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
    (tmp_path / "d_masked(2).txt").write_text("x")    # 既存出力(連番付き)
    (tmp_path / "e.pmap.csv").write_text("x")         # 対応表
    (tmp_path / "_report.txt").write_text("x")        # レポート
    r = walk(tmp_path)
    names = sorted(p.name for p in r.supported)
    assert names == ["a.txt", "b.md"]
    skipped_names = {p.name for p, _ in r.skipped}
    assert "c.pdf" in skipped_names
    assert "~$lock.docx" in skipped_names


def test_walk_does_not_skip_file_with_masked_substring_not_at_suffix(tmp_path):
    """最終レビュー Finding 8: "_masked" の部分一致ではなく、本アプリが
    実際に付与する末尾サフィックス（"_masked"/"_masked(N)"）のみを本アプリの
    出力として除外する。語幹の途中や先頭に偶然 "_masked" を含むだけの、
    本アプリ由来でない正当なユーザーファイルは、除外されず通常どおり
    supported に含まれる(services/pipeline.py の _restored_stem と同じ
    アンカー付き正規表現を再利用したことの回帰テスト)。"""
    (tmp_path / "already_masked_by_someone_else.txt").write_text("x")
    r = walk(tmp_path)
    names = {p.name for p in r.supported}
    assert "already_masked_by_someone_else.txt" in names
    skipped_names = {p.name for p, _ in r.skipped}
    assert "already_masked_by_someone_else.txt" not in skipped_names


def test_walk_still_skips_actual_masked_suffix_variants(tmp_path):
    (tmp_path / "report_masked.txt").write_text("x")
    (tmp_path / "report_masked(3).txt").write_text("x")
    r = walk(tmp_path)
    assert r.supported == []


def test_walk_skips_windows_hidden_and_system_file(tmp_path):
    # ファイル名は Unix 風の "." 始まりではない、ごく普通の名前。
    # Windows の隠し属性・システム属性（attrib +h +s / エクスプローラの「隠しファイル」）
    # のみで隠された実際のケースを再現する。
    target = tmp_path / "normal.txt"
    target.write_text("secret")
    ok = ctypes.windll.kernel32.SetFileAttributesW(
        str(target), _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM
    )
    assert ok, "SetFileAttributesW failed to set hidden/system attributes"

    r = walk(tmp_path)

    supported_names = {p.name for p in r.supported}
    assert "normal.txt" not in supported_names
    skipped_names = {p.name for p, _ in r.skipped}
    assert "normal.txt" in skipped_names


def test_walk_does_not_follow_ntfs_junction(tmp_path):
    # NTFS ジャンクションは IO_REPARSE_TAG_MOUNT_POINT を使い、シンボリックリンクとは
    # 別のタグのため Path.is_symlink() や os.walk(followlinks=False) では検出できない。
    # os.symlink() では真のジャンクションを再現できないため mklink /J で実際に作成する。
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    (real_target / "inside.txt").write_text("x")

    junction = tmp_path / "junction_link"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_target)],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr

    r = walk(tmp_path)

    # inside.txt は real_target 経由で1回だけ見えるべきで、junction 経由で二重に
    # 列挙されたり、循環構造で無限ループに陥ったりしてはならない。
    supported_names = sorted(p.name for p in r.supported)
    assert supported_names == ["inside.txt"]
    (only_hit,) = [p for p in r.supported if p.name == "inside.txt"]
    assert junction not in only_hit.parents


# --- walk_masked() のテスト ---

def test_walk_masked_collects_only_masked_files_with_handler(tmp_path):
    """walk_masked はマスク済み命名（*_masked / *_masked(N)）のファイルだけを
    集める。サポートされたハンドラがあること、サブディレクトリの再帰も含む。
    除外されるべきもの: 平文ファイル、未対応拡張子、対応表CSV、レポート、
    マスク済み命名だがハンドラなしの拡張子。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "plain.txt").write_text("x")           # 平文ファイル→除外
    (tmp_path / "sub" / "report_masked.txt").write_text("x")  # マスク済み→対象
    (tmp_path / "unknown_masked.pdf").write_text("x")   # マスク済みだが未対応拡張子→除外
    (tmp_path / "data_masked.xlsx").write_bytes(b"x")   # マスク済み→対象
    (tmp_path / "data_masked(2).xlsx").write_bytes(b"x")  # マスク済み(連番)→対象
    (tmp_path / "data_masked.txt.pmap.csv").write_text("x")  # 対応表→除外
    (tmp_path / "_report.txt").write_text("x")         # レポート→除外

    result = walk_masked(tmp_path)
    names = sorted(p.name for p in result)

    assert names == ["data_masked(2).xlsx", "data_masked.xlsx", "report_masked.txt"]


def test_walk_masked_skips_office_lock_file(tmp_path):
    """walk_masked は ~$ で始まるマスク済み命名のOfficeロックファイルを
    スキップする（e.g. ~$doc_masked.docx）。"""
    (tmp_path / "~$doc_masked.docx").write_bytes(b"x")  # ロックファイル→除外
    (tmp_path / "normal_masked.docx").write_bytes(b"x")  # 通常ファイル→対象

    result = walk_masked(tmp_path)
    names = sorted(p.name for p in result)

    assert names == ["normal_masked.docx"]


def test_walk_masked_skips_windows_hidden_and_system_file(tmp_path):
    """walk_masked は Windows 隠し属性・システム属性が設定されたマスク済み
    ファイルをスキップする。"""
    target = tmp_path / "secret_masked.txt"
    target.write_text("x")
    ok = ctypes.windll.kernel32.SetFileAttributesW(
        str(target), _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM
    )
    assert ok, "SetFileAttributesW failed"

    (tmp_path / "normal_masked.txt").write_text("x")

    result = walk_masked(tmp_path)
    names = sorted(p.name for p in result)

    assert names == ["normal_masked.txt"]
    assert "secret_masked.txt" not in names


def test_walk_masked_does_not_follow_ntfs_junction(tmp_path):
    """walk_masked は NTFS ジャンクションを辿らない。ジャンクション内の
    ファイルは real_target 経由で見えるべき。"""
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    (real_target / "file_masked.txt").write_text("x")

    junction = tmp_path / "junction_link"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_target)],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr

    result = walk_masked(tmp_path)

    # ファイルは real_target 経由で1回だけ見える
    names = sorted(p.name for p in result)
    assert names == ["file_masked.txt"]
    (only_hit,) = result
    assert junction not in only_hit.parents


def test_walk_masked_does_not_collect_masked_substring_not_at_suffix(tmp_path):
    """walk_masked は "_masked" を末尾サフィックスとしてのみ認識する。
    語幹の途中に "_masked" を含むだけのファイル
    （e.g. already_masked_by_someone_else.txt）は集めない。"""
    (tmp_path / "already_masked_by_someone_else_masked.txt").write_text("x")  # 末尾→対象
    (tmp_path / "already_masked_by_someone_else.txt").write_text("x")  # 途中→除外
    (tmp_path / "name_masked_in_middle_file.txt").write_text("x")  # 途中→除外

    result = walk_masked(tmp_path)
    names = sorted(p.name for p in result)

    # 末尾に "_masked" のあるものだけが集まる
    assert names == ["already_masked_by_someone_else_masked.txt"]
