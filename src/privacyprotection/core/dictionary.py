"""カスタム辞書による検出。"""
from __future__ import annotations

import re

from .models import Detection


class DictionaryDetector:
    def __init__(self, entries: dict[str, str]):
        self._entries = entries
        if entries:
            # 長い語句を優先（正規表現の選択肢は先勝ちのため長い順に並べる）
            alternation = "|".join(
                re.escape(w) for w in sorted(entries, key=len, reverse=True)
            )
            self._re: re.Pattern | None = re.compile(alternation)
        else:
            self._re = None

    def detect(self, text: str) -> list[Detection]:
        if self._re is None:
            return []
        return [
            Detection(
                text=m.group(), category=self._entries[m.group()],
                start=m.start(), end=m.end(), source="dictionary",
            )
            for m in self._re.finditer(text)
        ]
