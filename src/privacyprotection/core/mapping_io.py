"""対応表CSV（.pmap.csv）の読み書き（設計書 4.8）。"""
from __future__ import annotations

import csv
from pathlib import Path

from .models import CATEGORY_LABELS, LABEL_TO_CATEGORY, MappingEntry, MappingTable

_HEADER = ["トークン", "元の値", "種別", "出現回数"]
# 日本語ラベル→代表カテゴリの逆引きは core/models.py の LABEL_TO_CATEGORY に
# 一本化済み（復元には token/original しか使わないため代表値で可）。


def write_mapping(table: MappingTable, path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        for e in table.entries:
            # 手編集のconfig.jsonのカスタム辞書が固定10種以外のカテゴリを
            # 持っていた場合でもKeyErrorで落とさない（Finding 7。config.py の
            # export_dictionary_csv と同じフォールバック方針: ラベルが
            # 引けなければ生のカテゴリ文字列をそのまま書く）。read_mapping側は
            # 元々 LABEL_TO_CATEGORY.get(..., "CUSTOM") で未知ラベルを
            # "CUSTOM" に落とすため、この経路は往復しても安全に劣化する。
            writer.writerow(
                [e.token, e.original, CATEGORY_LABELS.get(e.category, e.category), e.count])


def read_mapping(path: Path) -> MappingTable:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != _HEADER:
            raise ValueError(f"対応表のヘッダーが不正です: {path.name}")
        entries = [
            MappingEntry(
                token=row[0], original=row[1],
                category=LABEL_TO_CATEGORY.get(row[2], "CUSTOM"),
                count=int(row[3]),
            )
            for row in reader if row
        ]
    seen_tokens: set[str] = set()
    for e in entries:
        if e.token in seen_tokens:
            raise ValueError(f"対応表に重複したトークンがあります: {e.token} ({path.name})")
        seen_tokens.add(e.token)
    return MappingTable(entries=entries)
