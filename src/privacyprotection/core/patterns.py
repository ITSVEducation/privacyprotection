"""定型PIIの正規表現検出。"""
from __future__ import annotations

import re

from .models import Detection
from .spans import remaining_spans

# 順序に意味あり: 長い形式（クレカ16桁）をマイナンバー(12桁)より先に評価
#
# CREDITCARD/MYNUMBER の前後境界ガードは意図的に (?<!\d) / (?!\d) の1文字
# 先読み・後読みのみを用いる。一度「数字+区切り文字」2文字分の先読み・後読み
# (?<!\d[- ]) / (?![- ]\d) に拡張したことがあった（756e712）が、レビューで
# regressionと判明したため差し戻し済み: 例えば「7-1234-5678-9012-3456」の
# 「1234-5678-9012-3456」部分は、直前の文字が区切り文字「-」であって数字では
# ない時点で、それ自体が独立した正規の16桁カード番号として成立している
# （先頭の「7-」は箇条書き番号など無関係な文脈である可能性が高い）。マスク漏れ
# （過小検出）は誤マスク（過検出）より有害なので、1文字ガードで十分かつ正しい。
# 「01234-5678-9012-3456」で「1234-5678-9012-3456」(index1開始)を誤って
# CREDITCARDとして検出しない、という本来防ぎたかったケースも、直前の文字
# (index0の"0")が数字であるため1文字ガードのみで既に正しく防止できている。
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CREDITCARD", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("MYNUMBER", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
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
                # 先に確定した検出と重なる場合、重ならない残り部分だけを採用する
                # （完全に重なりに覆われる場合は何も採用しない＝discard）
                for seg_start, seg_end in remaining_spans(span, taken):
                    taken.append((seg_start, seg_end))
                    results.append(Detection(
                        text=text[seg_start:seg_end], category=category,
                        start=seg_start, end=seg_end, source="pattern",
                    ))
        results.sort(key=lambda d: d.start)
        return results
