"""フォルダ再帰列挙（設計書 5.4）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .registry import get_handler


@dataclass
class WalkResult:
    supported: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _is_own_output(p: Path) -> bool:
    name = p.name
    return ("_masked" in p.stem) or name.endswith(".pmap.csv") or name == "_report.txt"


def walk(root: Path) -> WalkResult:
    result = WalkResult()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 隠しディレクトリは辿らない
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                result.skipped.append((p, "シンボリックリンク"))
                continue
            if name.startswith(("~$", ".")):
                result.skipped.append((p, "一時/隠しファイル"))
                continue
            if _is_own_output(p):
                continue  # 本アプリの出力物は黙って除外（レポート対象にもしない）
            if get_handler(p) is None:
                result.skipped.append((p, "未対応の拡張子"))
                continue
            result.supported.append(p)
    return result
