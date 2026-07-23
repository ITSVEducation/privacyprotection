"""xlsx。対象: 文字列セル・コメント・ヘッダーフッター・シート名（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

import openpyxl

from ..core.models import Fragment
from .base import FileHandler

_HF_PARTS = ("left", "center", "right")


class XlsxHandler(FileHandler):
    extensions = [".xlsx"]

    def read_fragments(self, path: Path) -> list[Fragment]:
        wb = openpyxl.load_workbook(path)
        frags: list[Fragment] = []
        for ws in wb.worksheets:
            frags.append(Fragment(text=ws.title, location=f"sheetname:{ws.title}"))
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and not cell.value.startswith("="):
                        frags.append(Fragment(
                            text=cell.value,
                            location=f"{ws.title}!{cell.coordinate}"))
                    if cell.comment is not None:
                        frags.append(Fragment(
                            text=cell.comment.text,
                            location=f"{ws.title}!{cell.coordinate}#comment"))
            hf_text = ws.oddHeader.center.text
            if hf_text:
                frags.append(Fragment(text=hf_text, location=f"{ws.title}#header"))
            ft_text = ws.oddFooter.center.text
            if ft_text:
                frags.append(Fragment(text=ft_text, location=f"{ws.title}#footer"))
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
            if f"{orig_title}#header" in by_loc:
                ws.oddHeader.center.text = by_loc[f"{orig_title}#header"]
            if f"{orig_title}#footer" in by_loc:
                ws.oddFooter.center.text = by_loc[f"{orig_title}#footer"]
            new_title = by_loc.get(f"sheetname:{orig_title}")
            if new_title and new_title != orig_title:
                ws.title = new_title
        wb.save(dst)
