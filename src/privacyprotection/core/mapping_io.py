"""対応表CSV（.pmap.csv）の読み書き（設計書 4.8）。"""
from __future__ import annotations

import csv
from pathlib import Path

from .models import CATEGORY_LABELS, MappingEntry, MappingTable

_HEADER = ["トークン", "元の値", "種別", "出現回数"]
_LABEL_TO_CATEGORY = {}  # 日本語ラベル→代表カテゴリ（復元には token/original しか使わないため代表値で可）
for cat, label in CATEGORY_LABELS.items():
    _LABEL_TO_CATEGORY.setdefault(label, cat)


def write_mapping(table: MappingTable, path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        for e in table.entries:
            writer.writerow([e.token, e.original, CATEGORY_LABELS[e.category], e.count])


def read_mapping(path: Path) -> MappingTable:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != _HEADER:
            raise ValueError(f"対応表のヘッダーが不正です: {path.name}")
        entries = [
            MappingEntry(
                token=row[0], original=row[1],
                category=_LABEL_TO_CATEGORY.get(row[2], "CUSTOM"),
                count=int(row[3]),
            )
            for row in reader if row
        ]
    return MappingTable(entries=entries)
