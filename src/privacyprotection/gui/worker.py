"""バックグラウンド処理ワーカー（設計書 N-4）。"""
from __future__ import annotations

import inspect
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class MaskWorker(QThread):
    progress = Signal(int, int, str)      # 現在, 総数, ファイル名
    finished_ok = Signal(object)          # 結果オブジェクト
    failed = Signal(str)                  # エラー要約（値は含めない）

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            kwargs = dict(self._kwargs)
            # fn が `progress` キーワード引数を受け付ける場合のみ自動的に
            # 進捗コールバックを差し込む。呼び出し側で明示的に指定済みなら
            # それを尊重する（setdefault）。
            #
            # 元プランの参考実装は `"progress_supported" in
            # self._kwargs.pop("_flags", [])` という条件で分岐していたが、
            # `_flags` は呼び出し元のどこからも渡されないため常に False
            # 判定になり、progress は一度も配線されない死んだコードだった
            # （pipeline.mask_folder の progress パラメータが実際には
            # 呼ばれず、GUIの進捗バーは実行中ずっと動かないままになる）。
            # `inspect.signature` で対象関数が `progress` を受け付けるか
            # 判定する方式なら、mask_folder のように progress をサポート
            # する呼び出しには常に正しく配線され、かつ progress を持たない
            # 別の関数（例えば restore 系）を将来 MaskWorker 経由で呼んでも
            # 予期しない TypeError にならない。
            if "progress" in inspect.signature(self._fn).parameters:
                kwargs.setdefault("progress", self._emit_progress)
            result = self._fn(*self._args, **kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: 処理に失敗しました")

    def _emit_progress(self, i: int, total: int, path: Path):
        self.progress.emit(i, total, path.name)
