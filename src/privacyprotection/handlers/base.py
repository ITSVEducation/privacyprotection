"""Handler共通インターフェース（設計書 4.5）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..core.models import Fragment


class FileHandler(ABC):
    extensions: ClassVar[list[str]] = []

    @abstractmethod
    def read_fragments(self, path: Path) -> list[Fragment]: ...

    @abstractmethod
    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None: ...
