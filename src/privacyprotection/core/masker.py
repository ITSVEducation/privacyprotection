"""置換エンジン（設計書 4.4）。"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import CATEGORY_LABELS, TOKEN_RE, Detection, MappingEntry, MappingTable

_REDACT = "●●●●"
_TOKEN_NUM_RE = re.compile(r"【(?:[^【】_]+)_(\d+)】")


class Masker:
    """検出結果に基づき原文断片を置換する（設計書 4.4）。

    同一の値（Detection.text）には、このインスタンスの生存期間中ずっと
    同一トークンを割り当てる。呼び出し側がフォルダ一括処理の全体で
    同一の Masker インスタンスを使い回すことで、複数ファイルにまたがる
    横断的な値の一貫性（同一人物は全ファイルで同一トークン）が実現される。
    """

    def __init__(self, mode: str):
        if mode not in ("token", "redact"):
            raise ValueError(f"unknown mode: {mode}")
        self._mode = mode
        self._counters: dict[str, int] = defaultdict(int)  # ラベル→直近に割り当てた連番
        self._value_tokens: dict[str, MappingEntry] = {}   # 元の値→エントリ

    def scan_existing_tokens(self, fragments: list[str]) -> int:
        """原文中の既存トークン形式を検出し、連番をその後ろから開始する。"""
        max_n = 0
        for frag in fragments:
            for m in TOKEN_RE.finditer(frag):
                num = int(_TOKEN_NUM_RE.fullmatch(m.group()).group(1))
                max_n = max(max_n, num)
        if max_n:
            for label in set(CATEGORY_LABELS.values()):
                self._counters[label] = max(self._counters[label], max_n)
        return max_n

    def _token_for(self, detection: Detection) -> str:
        entry = self._value_tokens.get(detection.text)
        if entry is None:
            label = CATEGORY_LABELS[detection.category]
            self._counters[label] += 1
            entry = MappingEntry(
                token=f"【{label}_{self._counters[label]}】",
                original=detection.text,
                category=detection.category,
                count=0,
            )
            self._value_tokens[detection.text] = entry
        return entry.token

    def mask_fragments(
        self, fragments: list[str], detections: list[list[Detection]]
    ) -> tuple[list[str], MappingTable]:
        out: list[str] = []
        for frag, dets in zip(fragments, detections):
            active = sorted((d for d in dets if d.enabled), key=lambda d: d.start)

            # 新規トークンの採番は、原文の出現順（開始位置の昇順=左から）で
            # 先に確定させる。置換そのものは後述のとおり右から（開始位置の
            # 降順で）行う必要があるが、採番の走査順と置換の走査順を同じに
            # すると、1断片内に同じラベルの未見の値が複数あるとき番号が
            # 読み順と逆転してしまう（例:「AとB」でBが先に置換されると
            # Bが_1、Aが_2になる）。_token_for は同一値に対して冪等なので、
            # ここで先に採番しておいても、後段の置換ループで再度呼んでも
            # 二重採番はされない。
            if self._mode == "token":
                for d in active:
                    self._token_for(d)

            text = frag
            for d in sorted(active, key=lambda d: d.start, reverse=True):
                # 置換は後ろ（開始位置の降順）から行うことで、それより前の
                # 部分の文字位置がずれないようにする。
                if self._mode == "token":
                    replacement = self._token_for(d)
                    self._value_tokens[d.text].count += 1
                else:
                    replacement = _REDACT
                text = text[:d.start] + replacement + text[d.end:]
            out.append(text)
        table = MappingTable(entries=list(self._value_tokens.values()))
        return out, table
