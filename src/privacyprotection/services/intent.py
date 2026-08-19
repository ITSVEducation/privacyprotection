"""入力からマスク/復元の意図を判定する純関数（設計書 04 §4.1）。

判定は決定的なルールのみで行う。GUI は復元と判定された場合に必ず確認
ダイアログを挟むため、誤判定の影響は「1回余計に確認される」に留まる。
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable

from ..core.models import TOKEN_RE
from ..handlers.folder_walker import MASKED_STEM_RE

FOLDER_MAPPING_NAME = "_folder.pmap.csv"


class Intent(Enum):
    MASK = "mask"
    RESTORE = "restore"


def _contains_tokens(texts: Iterable[str]) -> bool:
    return any(TOKEN_RE.search(t) for t in texts)


def _forced(action_mode: str) -> Intent | None:
    if action_mode == "mask":
        return Intent.MASK
    if action_mode == "restore":
        return Intent.RESTORE
    return None


def decide_text_intent(text: str, action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    return Intent.RESTORE if _contains_tokens([text]) else Intent.MASK


def decide_file_intent(path: Path, fragment_texts: list[str],
                       action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    if (path.parent / (path.name + ".pmap.csv")).exists():
        return Intent.RESTORE
    # _folder.pmap.csv は元ファイルの隣にも存在し得る（フォルダ一括処理は
    # 既定で同じフォルダに出力するため）。本アプリのマスク済み命名
    # （*_masked / *_masked(N)）のファイルに限って復元とみなす。
    if (MASKED_STEM_RE.match(path.stem)
            and (path.parent / FOLDER_MAPPING_NAME).exists()):
        return Intent.RESTORE
    if _contains_tokens(fragment_texts):
        return Intent.RESTORE
    return Intent.MASK


def decide_folder_intent(root: Path, action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    if (root / FOLDER_MAPPING_NAME).exists():
        return Intent.RESTORE
    return Intent.MASK
