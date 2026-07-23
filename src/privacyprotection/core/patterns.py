"""定型PIIの正規表現検出。"""
from __future__ import annotations

import re

from .models import Detection

# 順序に意味あり: 長い形式（クレカ16桁）をマイナンバー(12桁)より先に評価
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CREDITCARD", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("MYNUMBER", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}(?!\d)(?![- ]\d)")),
    ("PHONE", re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)|(?<!\d)0\d{9,10}(?!\d)")),
    ("POSTAL", re.compile(r"〒\s?\d{3}-\d{4}|(?<!\d)\d{3}-\d{4}(?!\d)")),
    ("ADDRESS", re.compile(
        r"(?:北海道|東京都|京都府|大阪府|[一-龠]{2,3}県)"
        r"[一-龠ぁ-んァ-ヶa-zA-Z0-9０-９\-ー−]{3,30}"
    )),
]


class PatternDetector:
    def detect(self, text: str) -> list[Detection]:
        results: list[Detection] = []
        taken: list[tuple[int, int]] = []
        for category, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                # 先に確定した検出と重なる範囲はスキップ（例: 郵便番号がクレカの一部にマッチ等）
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                taken.append(span)
                results.append(Detection(
                    text=m.group(), category=category,
                    start=m.start(), end=m.end(), source="pattern",
                ))
        results.sort(key=lambda d: d.start)
        return results
