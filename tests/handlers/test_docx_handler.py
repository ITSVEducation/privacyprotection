import zipfile

import docx
from privacyprotection.handlers.docx_handler import DocxHandler

h = DocxHandler()

def make_doc(tmp_path):
    d = docx.Document()
    d.add_paragraph("担当: 山田太郎")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "佐藤花子"
    t.cell(0, 1).text = "090-1111-2222"
    d.sections[0].header.paragraphs[0].text = "社外秘 山田太郎"
    d.sections[0].footer.paragraphs[0].text = "フッター 山田太郎"
    p = tmp_path / "test.docx"
    d.save(p)
    return p

def test_reads_paragraphs_tables_header(tmp_path):
    frags = h.read_fragments(make_doc(tmp_path))
    texts = {f.location: f.text for f in frags}
    assert texts["para:0"] == "担当: 山田太郎"
    assert texts["table:0:0:0"] == "佐藤花子"
    assert texts["table:0:0:1"] == "090-1111-2222"
    assert any(loc.startswith("header:") and "山田太郎" in t
               for loc, t in texts.items())

def test_reads_footer(tmp_path):
    frags = h.read_fragments(make_doc(tmp_path))
    texts = {f.location: f.text for f in frags}
    assert texts["footer:0:0"] == "フッター 山田太郎"

def test_write_replaces_text(tmp_path):
    src = make_doc(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.docx"
    h.write_fragments(src, dst, frags)
    d = docx.Document(str(dst))
    assert d.paragraphs[0].text == "担当: 【人名_1】"
    assert d.tables[0].cell(0, 0).text == "佐藤花子"
    assert "【人名_1】" in d.sections[0].header.paragraphs[0].text
    assert d.sections[0].footer.paragraphs[0].text == "フッター 【人名_1】"

def test_write_does_not_create_unused_header_footer_parts(tmp_path):
    # ヘッダー/フッターに一度も触れていない文書は、read_fragments/write_fragments
    # を通しても word/header*.xml・word/footer*.xml が新規作成されてはならない
    # （python-docxの遅延生成の副作用を回避できているかの回帰テスト）。
    d = docx.Document()
    d.add_paragraph("本文のみ 山田太郎")
    src = tmp_path / "plain.docx"
    d.save(src)

    with zipfile.ZipFile(src) as zsrc:
        src_names = set(zsrc.namelist())
    assert not any("header" in n or "footer" in n for n in src_names)

    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "plain_out.docx"
    h.write_fragments(src, dst, frags)

    # 出力ファイルの生の zip 内容を直接検査する。docx.Document(dst) で開いて
    # 改めて .header 等に触れると、その"新しい・未保存の"インメモリオブジェクト
    # 側でpartが生成されてしまい、既に保存済みのファイルの中身については何も
    # 証明できないため、zipfileで直接確認する。
    with zipfile.ZipFile(dst) as zdst:
        dst_names = set(zdst.namelist())
    new_names = dst_names - src_names
    assert not any("header" in n or "footer" in n for n in new_names)
    assert docx.Document(str(dst)).paragraphs[0].text == "本文のみ 【人名_1】"

def test_first_page_header_footer_masked(tmp_path):
    d = docx.Document()
    d.add_paragraph("本文")
    sec = d.sections[0]
    sec.different_first_page_header_footer = True
    sec.first_page_header.paragraphs[0].text = "先頭ページ見出し 山田太郎"
    sec.first_page_footer.paragraphs[0].text = "先頭ページ注記 山田太郎"
    src = tmp_path / "first_page.docx"
    d.save(src)

    frags = h.read_fragments(src)
    texts = {f.location: f.text for f in frags}
    assert texts["header:0:first:0"] == "先頭ページ見出し 山田太郎"
    assert texts["footer:0:first:0"] == "先頭ページ注記 山田太郎"
    # 通常(primary)ヘッダー/フッターは明示定義されていないので出現しない
    assert "header:0:0" not in texts
    assert "footer:0:0" not in texts

    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "first_page_out.docx"
    h.write_fragments(src, dst, frags)

    out = docx.Document(str(dst))
    out_sec = out.sections[0]
    assert out_sec.first_page_header.paragraphs[0].text == "先頭ページ見出し 【人名_1】"
    assert out_sec.first_page_footer.paragraphs[0].text == "先頭ページ注記 【人名_1】"

def test_even_page_header_footer_masked(tmp_path):
    d = docx.Document()
    d.add_paragraph("本文")
    d.settings.odd_and_even_pages_header_footer = True
    sec = d.sections[0]
    sec.even_page_header.paragraphs[0].text = "偶数ページ見出し 山田太郎"
    sec.even_page_footer.paragraphs[0].text = "偶数ページ注記 山田太郎"
    src = tmp_path / "even_page.docx"
    d.save(src)

    frags = h.read_fragments(src)
    texts = {f.location: f.text for f in frags}
    assert texts["header:0:even:0"] == "偶数ページ見出し 山田太郎"
    assert texts["footer:0:even:0"] == "偶数ページ注記 山田太郎"
    assert "header:0:0" not in texts
    assert "footer:0:0" not in texts

    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "even_page_out.docx"
    h.write_fragments(src, dst, frags)

    out = docx.Document(str(dst))
    out_sec = out.sections[0]
    assert out_sec.even_page_header.paragraphs[0].text == "偶数ページ見出し 【人名_1】"
    assert out_sec.even_page_footer.paragraphs[0].text == "偶数ページ注記 【人名_1】"

def test_set_paragraph_text_collapses_extra_runs(tmp_path):
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("担当: ").bold = True
    p.add_run("山田太郎").italic = True
    p.add_run("です")
    src = tmp_path / "multirun.docx"
    d.save(src)
    assert len(p.runs) == 3

    frags = h.read_fragments(src)
    texts = {f.location: f.text for f in frags}
    assert texts["para:0"] == "担当: 山田太郎です"

    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "multirun_out.docx"
    h.write_fragments(src, dst, frags)

    out = docx.Document(str(dst))
    out_p = out.paragraphs[0]
    assert out_p.text == "担当: 【人名_1】です"
    assert out_p.runs[0].text == "担当: 【人名_1】です"
    assert [r.text for r in out_p.runs[1:]] == [""] * (len(out_p.runs) - 1)
