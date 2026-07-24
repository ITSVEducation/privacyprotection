"""処理後レポート（設計書 6.4）。元の値そのものは決して含めない。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import CATEGORY_LABELS


@dataclass
class FileReport:
    path: Path
    category_counts: dict[str, int]
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def total(self) -> int:
        return sum(self.category_counts.values())


@dataclass
class BatchReport:
    files: list[FileReport] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    def zero_detection_files(self) -> list[FileReport]:
        return [f for f in self.files if f.error is None and f.total() == 0]

    def render_text(self) -> str:
        lines = ["=== マスク処理レポート ===", ""]
        for f in self.files:
            if f.error is not None:
                lines.append(f"[エラー] {f.path.name}: {f.error}")
                continue
            # 手編集のconfig.jsonに由来する固定10種以外のカテゴリ（Finding 7）
            # でもKeyErrorで落ちないよう、未知のカテゴリはラベルの代わりに
            # 生のカテゴリ文字列を表示する（config.py の export_dictionary_csv
            # と同じフォールバック方針）。
            counts = "、".join(
                f"{CATEGORY_LABELS.get(c, c)}: {n}" for c, n in sorted(f.category_counts.items())
            ) or "検出なし"
            warn = " ⚠ 検出0件（検出漏れの可能性があります。内容を確認してください）" \
                if f.total() == 0 else ""
            lines.append(f"{f.path.name}: {counts}{warn}")
            for note in f.notes:
                lines.append(f"  注記: {note}")
        if self.skipped:
            lines += ["", "--- 処理されなかったファイル ---"]
            lines += [f"{p.name}: {reason}" for p, reason in self.skipped]
        return "\n".join(lines)
