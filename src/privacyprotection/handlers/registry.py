"""拡張子→Handler解決。"""
from __future__ import annotations

from pathlib import Path

from .base import FileHandler
from .docx_handler import DocxHandler
from .pptx_handler import PptxHandler
from .text_handler import TextHandler
from .xlsx_handler import XlsxHandler

_HANDLERS: list[FileHandler] = [TextHandler(), XlsxHandler(), DocxHandler(), PptxHandler()]
_BY_EXT = {ext: h for h in _HANDLERS for ext in h.extensions}


def get_handler(path: Path) -> FileHandler | None:
    return _BY_EXT.get(path.suffix.lower())


def supported_extensions() -> list[str]:
    return sorted(_BY_EXT)
