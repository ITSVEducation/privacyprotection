"""定型PIIの正規表現検出。"""
from __future__ import annotations

import re

from .models import Detection

# 順序に意味あり: 長い形式（クレカ16桁）をマイナンバー(12桁)より先に評価
#
# CREDITCARD/MYNUMBER の前後境界ガードは (?<!\d) / (?!\d) の1文字先読み・後読みだけでは
# 不十分: 区切り文字(-や空白)を挟んで前後にさらに1桁以上の数字グループが続く場合
# （例:「7-1234-5678-9012-3456」や「1234-5678-9012-3456-7」）、1文字先読み・後読みは
# 区切り文字そのものは数字でないため通過してしまい、本来16桁/12桁ではない番号の
# 一部を誤って完全な形式として検出してしまう。そのため「数字+区切り文字」2文字分の
# 先読み・後読み (?<!\d[- ]) / (?![- ]\d) も併せて課し、両側を対称に防御する。
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CREDITCARD", re.compile(
        r"(?<!\d)(?<!\d[- ])\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)(?![- ]\d)"
    )),
    ("MYNUMBER", re.compile(
        r"(?<!\d)(?<!\d[- ])\d{4}[- ]\d{4}[- ]\d{4}(?!\d)(?![- ]\d)"
    )),
    ("PHONE", re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)|(?<!\d)0\d{9,10}(?!\d)")),
    ("POSTAL", re.compile(r"〒\s?\d{3}-\d{4}|(?<!\d)\d{3}-\d{4}(?!\d)")),
    ("ADDRESS", re.compile(
        r"(?:北海道|東京都|京都府|大阪府|[一-龠]{2,3}県)"
        r"[一-龠ぁ-んァ-ヶa-zA-Z0-9０-９\-ー−]{3,30}"
    )),
]


def _remaining_spans(
    span: tuple[int, int], taken: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """candidate span から、既に確定済みの taken 区間と重なる部分を除いた
    非重複の残り区間を返す（設計書4.2: 部分的な重複は重ならない残り部分を再検出）。

    - taken と全く重ならなければ span をそのまま1件返す。
    - taken（複数の場合はその和集合）に完全に覆われていれば空リストを返す。
    - それ以外（部分重複）は、重ならない部分だけを1件以上の区間として返す
      （taken が候補の中央にある場合は前後2区間に分かれることもある）。
    """
    start, end = span
    overlapping = sorted(
        (max(s, start), min(e, end))
        for s, e in taken
        if s < end and start < e
    )
    if not overlapping:
        return [span]

    remaining: list[tuple[int, int]] = []
    cursor = start
    for s, e in overlapping:
        if s > cursor:
            remaining.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end:
        remaining.append((cursor, end))
    return remaining


class PatternDetector:
    def detect(self, text: str) -> list[Detection]:
        results: list[Detection] = []
        taken: list[tuple[int, int]] = []
        for category, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                # 先に確定した検出と重なる場合、重ならない残り部分だけを採用する
                # （完全に重なりに覆われる場合は何も採用しない＝discard）
                for seg_start, seg_end in _remaining_spans(span, taken):
                    taken.append((seg_start, seg_end))
                    results.append(Detection(
                        text=text[seg_start:seg_end], category=category,
                        start=seg_start, end=seg_end, source="pattern",
                    ))
        results.sort(key=lambda d: d.start)
        return results
