"""検出器のマージと重複解決（設計書 4.2）。"""
from __future__ import annotations

from .models import Detection
# 区間差分ロジック・重複解決アルゴリズムは patterns.py の PatternDetector や
# Masker.mask_fragments() と共有（core/spans.py に一本化）。
# `_remaining_spans` の名前は既存の直接インポート（tests/core/test_detector.py）との
# 後方互換のため、インポート時のエイリアスとして維持している（ローカル実装ではない）。
from .spans import remaining_spans as _remaining_spans, resolve_overlaps

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

    上記ルールの実装そのものは `core/spans.py` の `resolve_overlaps()` に
    一本化されている（`Masker.mask_fragments()` も同じ関数を使う）。ここで
    独自に同じソート・trim-not-discardロジックを再実装しない — 過去に
    ここへ手書きで複製されていたが（Task 18 レビューで指摘）、片方だけ
    修正されもう片方に同じ不具合が残るリスクがあるため、共有関数の呼び出し
    に置き換えた。
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

        # 長い範囲優先 → ソース優先度 → 開始位置の順の重複解決（設計書4.2）は
        # `resolve_overlaps()` に委譲する（trim-not-discardの実装は共有の
        # 一箇所のみに存在する）。
        return resolve_overlaps(candidates, text, _SOURCE_PRIORITY)
