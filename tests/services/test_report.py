from pathlib import Path
from privacyprotection.services.report import BatchReport, FileReport


def test_file_report_total():
    r = FileReport(Path("a.txt"), {"PERSON": 3, "PHONE": 1}, [])
    assert r.total() == 4

def test_zero_detection_files_highlighted():
    ok = FileReport(Path("a.txt"), {"PERSON": 1}, [])
    zero = FileReport(Path("b.txt"), {}, [])
    err = FileReport(Path("c.txt"), {}, [], error="読み込み失敗")
    br = BatchReport(files=[ok, zero, err], skipped=[])
    assert [f.path.name for f in br.zero_detection_files()] == ["b.txt"]

def test_render_text_contains_counts_warnings_and_no_values():
    br = BatchReport(
        files=[
            FileReport(Path("a.txt"), {"PERSON": 2}, ["図形内テキストは対象外です"]),
            FileReport(Path("b.txt"), {}, []),
        ],
        skipped=[(Path("c.pdf"), "未対応の拡張子")],
    )
    text = br.render_text()
    assert "a.txt" in text and "人名: 2" in text
    assert "⚠ 検出0件" in text and "b.txt" in text
    assert "c.pdf" in text and "未対応の拡張子" in text
    assert "図形内テキストは対象外です" in text
