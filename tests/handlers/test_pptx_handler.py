import zipfile

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.oxml.ns import qn
from pptx.util import Inches
from privacyprotection.core.models import Fragment
from privacyprotection.handlers.pptx_handler import PptxHandler

h = PptxHandler()

def make_pptx(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text = "山田太郎の報告"
    box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "連絡先: 090-1111-2222"
    slide.notes_slide.notes_text_frame.text = "佐藤花子に確認"
    p = tmp_path / "test.pptx"
    prs.save(p)
    return p

def test_reads_shapes_and_notes(tmp_path):
    frags = h.read_fragments(make_pptx(tmp_path))
    all_text = " ".join(f.text for f in frags)
    assert "山田太郎の報告" in all_text
    assert "連絡先: 090-1111-2222" in all_text
    assert "佐藤花子に確認" in all_text

def test_write_replaces_text(tmp_path):
    src = make_pptx(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.pptx"
    h.write_fragments(src, dst, frags)
    out_frags = h.read_fragments(dst)
    all_text = " ".join(f.text for f in out_frags)
    assert "【人名_1】の報告" in all_text
    assert "山田太郎" not in all_text


# --- Finding 1 (Critical): grouped shapes must not be invisible to read/write ---

def test_reads_and_masks_shape_inside_group(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "田中一郎です"
    slide.shapes.add_group_shape([box])
    src = tmp_path / "group.pptx"
    prs.save(src)

    frags = h.read_fragments(src)
    all_text = " ".join(f.text for f in frags)
    assert "田中一郎です" in all_text

    for f in frags:
        f.text = f.text.replace("田中一郎", "【人名_1】")
    dst = tmp_path / "group_out.pptx"
    h.write_fragments(src, dst, frags)

    out = Presentation(dst)
    group = out.slides[0].shapes[0]
    assert group.shape_type == MSO_SHAPE_TYPE.GROUP
    member_text = group.shapes[0].text_frame.text
    assert member_text == "【人名_1】です"
    assert "田中一郎" not in member_text

def test_reads_and_masks_shape_inside_nested_group(tmp_path):
    """グループの中にさらにグループがある、任意の深さのネストを確認する。"""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "鈴木花子"
    inner_group = slide.shapes.add_group_shape([box])
    slide.shapes.add_group_shape([inner_group])
    src = tmp_path / "nested_group.pptx"
    prs.save(src)

    frags = h.read_fragments(src)
    all_text = " ".join(f.text for f in frags)
    assert "鈴木花子" in all_text

    for f in frags:
        f.text = f.text.replace("鈴木花子", "【人名_1】")
    dst = tmp_path / "nested_group_out.pptx"
    h.write_fragments(src, dst, frags)

    out = Presentation(dst)
    outer_group = out.slides[0].shapes[0]
    inner = outer_group.shapes[0]
    leaf = inner.shapes[0]
    assert leaf.text_frame.text == "【人名_1】"


# --- Finding 2 (Important): must not create <p:txBody> in shapes that never had one ---

def test_write_does_not_add_txbody_to_textless_shape(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "連絡先: 090-1111-2222"
    deco = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(4), Inches(1), Inches(1), Inches(1))
    deco_id = deco.shape_id

    # add_shape()は既定で（テキストが一度も入力されていなくても）空の
    # <p:txBody>を持つため、一度もテキストが入力されていない「本物の」装飾
    # シェイプを再現するにはXMLレベルで取り除く必要がある。
    sp_elem = deco.element
    txBody = sp_elem.find(qn("p:txBody"))
    assert txBody is not None  # 前提の確認: add_shape()は既定で空のtxBodyを持つ
    sp_elem.remove(txBody)
    src = tmp_path / "deco.pptx"
    prs.save(src)

    frags = h.read_fragments(src)
    dst = tmp_path / "deco_out.pptx"
    h.write_fragments(src, dst, frags)

    # python-pptxで開いて.text_frameに触れると新規オブジェクト上でtxBodyが
    # 作られてしまい、保存済みファイルの検証にならない。保存されたXMLを
    # 直接(lxml経由で)検査する。
    with zipfile.ZipFile(dst) as z:
        xml_bytes = z.read("ppt/slides/slide1.xml")
    root = etree.fromstring(xml_bytes)
    for sp in root.iter(qn("p:sp")):
        cNvPr = sp.find(f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}")
        if cNvPr is not None and int(cNvPr.get("id")) == deco_id:
            assert sp.find(qn("p:txBody")) is None
            break
    else:
        raise AssertionError("対象の装飾シェイプがslide1.xmlに見つからない")

def test_write_sets_text_on_run_less_paragraph(tmp_path):
    """runを一つも持たない段落(paragraph.textセッター経路)への書き込みを確認する。"""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(1))
    src = tmp_path / "empty_para.pptx"
    prs.save(src)

    reopened = Presentation(src)
    para = reopened.slides[0].shapes[0].text_frame.paragraphs[0]
    assert not para.runs  # 前提の確認: runを持たない空の段落

    dst = tmp_path / "empty_para_out.pptx"
    h.write_fragments(
        src, dst, [Fragment(text="新しい文言", location="slide:0:shape:0:para:0")])

    out = Presentation(dst)
    assert out.slides[0].shapes[0].text_frame.text == "新しい文言"


# --- Finding 3 (Important): notes slide present but notes placeholder deleted ---

def test_notes_placeholder_missing_does_not_crash(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "高橋次郎"
    slide.notes_slide.notes_text_frame.text = "備考"

    notes_slide = slide.notes_slide
    ph = notes_slide.notes_placeholder
    assert ph is not None  # 前提の確認
    ph.element.getparent().remove(ph.element)
    assert notes_slide.notes_placeholder is None  # 削除できたことの確認
    assert notes_slide.notes_text_frame is None

    src = tmp_path / "no_notes_ph.pptx"
    prs.save(src)

    frags = h.read_fragments(src)  # 例外(AttributeError)が起きないこと
    all_text = " ".join(f.text for f in frags)
    assert "高橋次郎" in all_text
    assert "備考" not in all_text  # プレースホルダー削除済みなので拾われない

    dst = tmp_path / "no_notes_ph_out.pptx"
    h.write_fragments(src, dst, frags)  # 例外が起きないこと
    out_frags = h.read_fragments(dst)
    assert " ".join(f.text for f in out_frags) == all_text
