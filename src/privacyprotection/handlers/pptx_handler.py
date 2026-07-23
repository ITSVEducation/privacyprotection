"""pptx。対象: シェイプテキスト・表・スピーカーノート（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from ..core.models import Fragment
from .base import FileHandler


def _set_paragraph_text(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.text = text


class PptxHandler(FileHandler):
    extensions = [".pptx"]

    def _iter_locations(self, prs):
        for si, slide in enumerate(prs.slides):
            for hi, shape in enumerate(slide.shapes):
                if shape.has_text_frame:
                    for pi, para in enumerate(shape.text_frame.paragraphs):
                        yield f"slide:{si}:shape:{hi}:para:{pi}", para
                if shape.has_table:
                    for ri, row in enumerate(shape.table.rows):
                        for ci, cell in enumerate(row.cells):
                            for pi, para in enumerate(cell.text_frame.paragraphs):
                                yield f"slide:{si}:table:{hi}:{ri}:{ci}:para:{pi}", para
            if slide.has_notes_slide:
                for pi, para in enumerate(
                        slide.notes_slide.notes_text_frame.paragraphs):
                    yield f"slide:{si}:notes:para:{pi}", para

    @staticmethod
    def _para_text(para) -> str:
        return "".join(run.text for run in para.runs)

    def read_fragments(self, path: Path) -> list[Fragment]:
        prs = Presentation(str(path))
        return [Fragment(text=self._para_text(p), location=loc)
                for loc, p in self._iter_locations(prs) if self._para_text(p)]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        prs = Presentation(str(src))
        by_loc = {f.location: f.text for f in masked}
        for loc, para in self._iter_locations(prs):
            if loc in by_loc and self._para_text(para) != by_loc[loc]:
                _set_paragraph_text(para, by_loc[loc])
        prs.save(str(dst))
