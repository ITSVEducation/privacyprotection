"""区間差分（span subtraction）の共有ロジック。

candidate span から、既に確定済みの taken 区間群と重なる部分を除いた非重複の
残り区間を計算する（設計書4.2: 部分的な重複は重ならない残り部分を再検出する）。

`patterns.py` の `PatternDetector` と `detector.py` の `Detector` はどちらも
重複解決処理でこの同一アルゴリズムを必要とするため、二重実装を避けてここに
一本化する（レビュー済み・承認済みの区間差分ロジック）。
"""
from __future__ import annotations

from .models import Detection


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


def resolve_overlaps(
    candidates: list[Detection], text: str, priority: dict[str, int],
) -> list[Detection]:
    """任意の `Detection` 候補集合を、設計書4.2の重複解決ルール（長い範囲を
    優先し、同じ長さなら `priority` の値が小さいほうを優先。部分重複は
    重ならない残り部分をtrimして残す＝丸ごと破棄しない）に従ってマージする。

    `detector.py` の `Detector.detect()` 内で使われているのと同じアルゴリズム
    （ソート基準・`remaining_spans` によるtrim-not-discard）だが、`Detector` が
    扱う3種類の自動検出ソース（dictionary/pattern/ner）に限定されない任意の
    `priority` マップを受け取れるよう一般化したもの。呼び出し側が渡す
    `Detection` の `source` にどんな値が入っていても、`priority` に無ければ
    最低優先度（末尾）として扱う。

    Task 18: 手動追加された Detection（source="manual"）が既存の自動検出と
    重なった場合に、そのまま `Masker.mask_fragments()` に渡すと重複した区間を
    前提とする置換ロジックが破綻し、原文が壊れる／検出漏れになる不具合への
    対策として追加した。`Masker.mask_fragments()` が、置換対象の
    「有効(enabled)なDetection集合」に対してこの関数を通すことで、
    手動追加がどんな範囲を選んでいても置換前に必ず非重複な集合へ解決される。
    """
    ordered = sorted(candidates, key=lambda c: (
        -(c.end - c.start), priority.get(c.source, 9), c.start,
    ))
    taken: list[tuple[int, int]] = []
    accepted: list[Detection] = []
    for c in ordered:
        for seg_start, seg_end in remaining_spans((c.start, c.end), taken):
            taken.append((seg_start, seg_end))
            accepted.append(Detection(
                text=text[seg_start:seg_end], category=c.category,
                start=seg_start, end=seg_end, source=c.source,
                enabled=c.enabled,
            ))
    accepted.sort(key=lambda d: d.start)
    return accepted
