"""検出器のマージと重複解決（設計書 4.2）。"""
from __future__ import annotations

from .models import Detection
# 区間差分ロジックは patterns.py の PatternDetector と共有（core/spans.py に一本化）。
# `_remaining_spans` の名前は既存の直接インポート（tests/core/test_detector.py）との
# 後方互換のため、インポート時のエイリアスとして維持している（ローカル実装ではない）。
from .spans import remaining_spans as _remaining_spans

_SOURCE_PRIORITY = {"dictionary": 0, "pattern": 1, "ner": 2}


class Detector:
    """複数の検出器（pattern/dictionary/ner 等、`.detect(text) -> list[Detection]`
    を持つ任意のオブジェクト）の結果をマージし、重複を解決する（設計書 4.2）。

    重複解決ルール（設計書 4.2）:
      1. 長い範囲を優先。
      2. 同じ長さなら source 優先度 dictionary > pattern > ner。
      3. 部分的に重なり合い包含関係にない場合は、開始位置が先のものを採用し、
         後のものは重複しない残り部分を再検出する（丸ごと破棄しない）。
      4. 既存の採用済み範囲に完全に覆われる場合のみ、候補を丸ごと破棄する。
    """

    def __init__(self, detectors: list, enabled_categories: set[str] | None = None):
        self._detectors = detectors
        self._enabled = enabled_categories

    def detect(self, text: str) -> list[Detection]:
        candidates: list[Detection] = []
        for det in self._detectors:
            candidates.extend(det.detect(text))

        if self._enabled is not None:
            candidates = [c for c in candidates if c.category in self._enabled]

        # 長い範囲優先 → ソース優先度 → 開始位置の順で採用候補を並べる。
        # この順序がそのまま「重複時にどちらが勝つか」の処理順になる。
        candidates.sort(key=lambda c: (
            -(c.end - c.start),
            _SOURCE_PRIORITY.get(c.source, 9),
            c.start,
        ))

        taken: list[tuple[int, int]] = []
        accepted: list[Detection] = []
        for c in candidates:
            # 既に確定済みの区間(taken)と重ならない残り部分だけを採用する。
            # 完全に覆われる場合は _remaining_spans が空リストを返し、
            # 候補は丸ごと破棄される。部分重複の場合は、生き残った
            # 断片ごとに元候補の category/source を引き継いだ新しい
            # Detection を作る（text は元候補のものを使い回さず、必ず
            # text[sub_start:sub_end] から切り出す＝候補が短くなっている
            # 可能性があるため）。
            for seg_start, seg_end in _remaining_spans((c.start, c.end), taken):
                taken.append((seg_start, seg_end))
                accepted.append(Detection(
                    text=text[seg_start:seg_end],
                    category=c.category,
                    start=seg_start,
                    end=seg_end,
                    source=c.source,
                ))

        accepted.sort(key=lambda d: d.start)
        return accepted
