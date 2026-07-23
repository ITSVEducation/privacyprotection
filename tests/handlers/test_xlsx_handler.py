import openpyxl
from openpyxl.comments import Comment
from privacyprotection.handlers.xlsx_handler import XlsxHandler

h = XlsxHandler()

def make_book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "名簿"
    ws["A1"] = "山田太郎"
    ws["B1"] = 42          # 数値 → 対象外
    ws["A2"] = "=SUM(1,2)" # 数式 → 対象外
    ws["A3"] = "佐藤花子"
    ws["A3"].comment = Comment("連絡先: 090-1111-2222", "author")
    ws.oddHeader.center.text = "社外秘 山田太郎"
    p = tmp_path / "test.xlsx"
    wb.save(p)
    return p

def test_reads_string_cells_comments_header_sheetname(tmp_path):
    frags = h.read_fragments(make_book(tmp_path))
    texts = {f.location: f.text for f in frags}
    assert texts["名簿!A1"] == "山田太郎"
    assert texts["名簿!A3"] == "佐藤花子"
    assert texts["名簿!A3#comment"] == "連絡先: 090-1111-2222"
    assert texts["名簿#header"] == "社外秘 山田太郎"
    assert texts["sheetname:名簿"] == "名簿"
    assert "名簿!B1" not in texts   # 数値は対象外
    assert "名簿!A2" not in texts   # 数式は対象外

def test_write_replaces_only_masked_text(tmp_path):
    src = make_book(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.xlsx"
    h.write_fragments(src, dst, frags)
    wb = openpyxl.load_workbook(dst)
    ws = wb["名簿"]
    assert ws["A1"].value == "【人名_1】"
    assert ws["B1"].value == 42
    assert ws["A3"].value == "佐藤花子"
    assert ws.oddHeader.center.text == "社外秘 【人名_1】"
