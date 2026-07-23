"""区間差分（span subtraction）の共有ロジック。

candidate span から、既に確定済みの taken 区間群と重なる部分を除いた非重複の
残り区間を計算する（設計書4.2: 部分的な重複は重ならない残り部分を再検出する）。

`patterns.py` の `PatternDetector` と `detector.py` の `Detector` はどちらも
重複解決処理でこの同一アルゴリズムを必要とするため、二重実装を避けてここに
一本化する（レビュー済み・承認済みの区間差分ロジック）。
"""
from __future__ import annotations


def remaining_spans(
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
