"""docx。対象: 本文段落・表・ヘッダーフッター（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

import docx

from ..core.models import Fragment
from .base import FileHandler


def _set_paragraph_text(paragraph, text: str) -> None:
    """段落テキストを差し替える。書式は先頭runのものに揃う。"""
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


class DocxHandler(FileHandler):
    extensions = [".docx"]

    def _iter_locations(self, doc):
        for i, p in enumerate(doc.paragraphs):
            yield f"para:{i}", p
        for ti, table in enumerate(doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        loc = f"table:{ti}:{ri}:{ci}" if pi == 0 else f"table:{ti}:{ri}:{ci}:p{pi}"
                        yield loc, p
        for si, section in enumerate(doc.sections):
            for pi, p in enumerate(section.header.paragraphs):
                yield f"header:{si}:{pi}", p
            for pi, p in enumerate(section.footer.paragraphs):
                yield f"footer:{si}:{pi}", p

    def read_fragments(self, path: Path) -> list[Fragment]:
        doc = docx.Document(str(path))
        return [Fragment(text=p.text, location=loc)
                for loc, p in self._iter_locations(doc) if p.text]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        doc = docx.Document(str(src))
        by_loc = {f.location: f.text for f in masked}
        for loc, p in self._iter_locations(doc):
            if loc in by_loc and p.text != by_loc[loc]:
                _set_paragraph_text(p, by_loc[loc])
        doc.save(str(dst))
