"""フォルダ再帰列挙（設計書 5.4）。"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .registry import get_handler

# Windows のファイル属性ビット。stat モジュールの定数自体はプラットフォームを問わず
# 定義されているが、os.lstat().st_file_attributes の値は Windows 以外では意味を持たない
# （常に 0 相当）。本アプリは Windows 専用なのでそれで問題ない。
_HIDDEN_OR_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
_REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT

# services/pipeline.py の mask_file() が付与する連番付きサフィックス
# （"_masked" または "_masked(N)"）。ここ(handlers層)で定義し、
# services/pipeline.py 側がこれをimportして使う（services→handlersという
# 既存の依存方向を保ったまま、両者が同じ「自アプリ出力の見分け方」を共有
# できるようにするため）。
MASKED_SUFFIX = "_masked"
# 語幹の「末尾」からのみサフィックスを取り除く/検出するためのパターン。
# 単純な "_masked" in stem という部分一致(旧実装)は、語幹の途中に偶然
# "_masked" を含む名前（本アプリ由来でないファイル、例:
# "already_masked_by_someone_else.txt"）まで自アプリの出力と誤認して
# しまう（最終レビュー Finding 8）ため使わない。
MASKED_STEM_RE = re.compile(r"^(?P<base>.*)" + re.escape(MASKED_SUFFIX) + r"(?:\(\d+\))?$")


@dataclass
class WalkResult:
    supported: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _file_attributes(p: Path) -> int:
    """os.lstat() が返す Windows ファイル属性ビットを返す。

    シンボリックリンク・ジャンクション（リパースポイント）そのものの属性を見る必要が
    あるため、リンク先を解決してしまう os.stat() ではなく os.lstat() を使う。
    参照先が既に存在しない等でアクセスできない場合は 0（通常ファイル扱い）として
    列挙処理全体を止めないようにする。
    """
    try:
        return os.lstat(p).st_file_attributes
    except OSError:
        return 0


def _is_hidden_or_system(p: Path) -> bool:
    """Windows の隠し属性・システム属性（attrib +h / +s、エクスプローラの「隠しファイル」
    チェックボックス等）が設定されているか。Unix 風の `.` 始まりの名前とは別物。
    """
    return bool(_file_attributes(p) & _HIDDEN_OR_SYSTEM)


def _is_reparse_point(p: Path) -> bool:
    """シンボリックリンク・NTFS ジャンクション等のリパースポイントか。

    NTFS ジャンクションは IO_REPARSE_TAG_MOUNT_POINT を使っており、
    Path.is_symlink()（IO_REPARSE_TAG_SYMLINK のみを見る）や os.walk の
    followlinks=False では検出できないため捕捉できない。属性ビット
    FILE_ATTRIBUTE_REPARSE_POINT を直接見ることで両方を判定する。
    """
    return bool(_file_attributes(p) & _REPARSE_POINT)


def _is_own_output(p: Path) -> bool:
    name = p.name
    # "_masked" の部分一致ではなく、本アプリが実際に付与する末尾サフィックス
    # （"_masked"/"_masked(N)"）のみに限定して判定する（Finding 8）。
    return bool(MASKED_STEM_RE.match(p.stem)) or name.endswith(".pmap.csv") or name == "_report.txt"


def _prune_dirs(dirpath: str, dirnames: list[str]) -> None:
    """os.walk が次に降りるディレクトリから、辿ってはいけないものを取り除く
    （in-place。設計書 2.8）。

    隠し/システムディレクトリ、シンボリックリンク・ジャンクションは辿らない
    （無限ループ防止）。os.walk の followlinks=False 自体はジャンクションを
    止められないため、dirnames をその場でフィルタして列挙元で止める。
    """
    kept = []
    for d in dirnames:
        dp = Path(dirpath) / d
        if d.startswith("."):
            continue
        if _is_reparse_point(dp):
            continue
        if _is_hidden_or_system(dp):
            continue
        kept.append(d)
    dirnames[:] = kept


def _skip_reason(p: Path) -> str | None:
    """列挙から外すべきファイルならその理由、対象にできるなら None
    （設計書 2.8 の境界条件）。walk() は理由をレポート用の skipped に
    載せ、walk_masked() は理由を使わず単に除外する。
    """
    if p.is_symlink() or _is_reparse_point(p):
        return "シンボリックリンク/ジャンクション"
    if _is_hidden_or_system(p):
        return "隠し/システムファイル"
    if p.name.startswith(("~$", ".")):
        return "一時/隠しファイル"
    return None


def walk(root: Path) -> WalkResult:
    result = WalkResult()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        _prune_dirs(dirpath, dirnames)

        for name in sorted(filenames):
            p = Path(dirpath) / name
            reason = _skip_reason(p)
            if reason is not None:
                result.skipped.append((p, reason))
                continue
            if _is_own_output(p):
                continue  # 本アプリの出力物は黙って除外（レポート対象にもしない）
            if get_handler(p) is None:
                result.skipped.append((p, "未対応の拡張子"))
                continue
            result.supported.append(p)
    return result


def walk_masked(root: Path) -> list[Path]:
    """本アプリのマスク済み出力（`*_masked` / `*_masked(N)`）だけを再帰列挙する
    （フォルダ一括復元の対象。設計書 2.8）。

    境界条件は walk() と同じものを共有する（`_prune_dirs` / `_skip_reason`）:
    シンボリックリンク・NTFS ジャンクションは辿らず、隠し/システムファイルと
    `~$` で始まる Office ロックファイルはスキップする。walk() が自アプリの
    出力を除外するのに対し、こちらは逆に自アプリの出力だけを集める — 復元の
    対象がまさにそれだから。対応表CSV（`*.pmap.csv`）とレポート（`_report.txt`）は
    マスク済み命名に一致しないため自然に外れる。
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        _prune_dirs(dirpath, dirnames)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if _skip_reason(p) is not None:
                continue
            if not MASKED_STEM_RE.match(p.stem):
                continue
            if get_handler(p) is None:
                continue
            found.append(p)
    return found
