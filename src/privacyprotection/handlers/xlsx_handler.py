"""xlsx。対象: 文字列セル・コメント・ヘッダーフッター・シート名（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

import openpyxl

from ..core.models import Fragment
from .base import FileHandler

# ヘッダー/フッターは奇数/偶数/先頭ページ(Excelの「先頭ページのみ別指定」設定)の
# 3種 × 左/中央/右の3パーツ、計9箇所ずつ存在する（openpyxlの
# oddHeader/evenHeader/firstHeader, oddFooter/evenFooter/firstFooter が対応）。
_HF_VARIANTS = ("odd", "even", "first")
_HF_PARTS = ("left", "center", "right")
# location文字列に使う種別名 → openpyxl属性名サフィックスの対応
_HF_KINDS = (("header", "Header"), ("footer", "Footer"))


def _hf_location(kind: str, title: str, variant: str, part: str) -> str:
    # 奇数(odd)+中央(center)は元々の唯一の対象箇所だったため、既存の呼び出し元
    # との後方互換のためlocation文字列を変更しない。それ以外の8通り(header/
    # footerそれぞれ)は variant:part を付与して区別する。
    if variant == "odd" and part == "center":
        return f"{title}#{kind}"
    return f"{title}#{kind}:{variant}:{part}"


class XlsxHandler(FileHandler):
    extensions = [".xlsx"]

    def read_fragments(self, path: Path) -> list[Fragment]:
        wb = openpyxl.load_workbook(path)
        frags: list[Fragment] = []
        for ws in wb.worksheets:
            frags.append(Fragment(text=ws.title, location=f"sheetname:{ws.title}"))
            for row in ws.iter_rows():
                for cell in row:
                    # 文字列セルかどうかは cell.data_type == "s" で判定する。
                    # 値が "=" で始まるかどうかの文字列判定では、"=" から
                    # 始まる文字列リテラルセル（数式ではない）を誤って数式
                    # 扱いし、PII検出対象から漏らしてしまうため使わない。
                    # data_type は "s"(文字列)/"f"(数式)/"n"(数値) 等を
                    # openpyxlが保持している権威ある値。
                    if cell.data_type == "s":
                        frags.append(Fragment(
                            text=cell.value,
                            location=f"{ws.title}!{cell.coordinate}"))
                    if cell.comment is not None:
                        frags.append(Fragment(
                            text=cell.comment.text,
                            location=f"{ws.title}!{cell.coordinate}#comment"))
            for kind, attr_suffix in _HF_KINDS:
                for variant in _HF_VARIANTS:
                    hf = getattr(ws, f"{variant}{attr_suffix}")
                    for part in _HF_PARTS:
                        text = getattr(hf, part).text
                        if text:
                            frags.append(Fragment(
                                text=text,
                                location=_hf_location(kind, ws.title, variant, part)))
        return frags

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        wb = openpyxl.load_workbook(src)
        by_loc = {f.location: f.text for f in masked}
        for ws in wb.worksheets:
            orig_title = ws.title
            for row in ws.iter_rows():
                for cell in row:
                    loc = f"{orig_title}!{cell.coordinate}"
                    if loc in by_loc:
                        cell.value = by_loc[loc]
                    cloc = f"{loc}#comment"
                    if cloc in by_loc and cell.comment is not None:
                        cell.comment.text = by_loc[cloc]
            for kind, attr_suffix in _HF_KINDS:
                for variant in _HF_VARIANTS:
                    hf = getattr(ws, f"{variant}{attr_suffix}")
                    for part in _HF_PARTS:
                        loc = _hf_location(kind, orig_title, variant, part)
                        if loc in by_loc:
                            getattr(hf, part).text = by_loc[loc]
            new_title = by_loc.get(f"sheetname:{orig_title}")
            if new_title and new_title != orig_title:
                ws.title = new_title
        wb.save(dst)
