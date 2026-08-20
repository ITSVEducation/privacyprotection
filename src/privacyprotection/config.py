"""設定・カスタム辞書の永続化（設計書 6.3）。保存先: %APPDATA%/PrivacyProtection/"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .core.models import CATEGORY_LABELS, LABEL_TO_CATEGORY

ALL_CATEGORIES = set(CATEGORY_LABELS)


def default_config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "PrivacyProtection"
    return base / "config.json"


@dataclass
class AppConfig:
    enabled_categories: set[str] = field(default_factory=lambda: set(ALL_CATEGORIES))
    custom_dictionary: dict[str, str] = field(default_factory=dict)
    output_dir: str | None = None
    skip_preview: bool = False
    mask_mode: str = "token"
    action_mode: str = "auto"          # "auto" | "mask" | "restore"
    advanced_expanded: bool = False    # 詳細設定パネルの開閉状態


def load_config(path: Path | None = None) -> AppConfig:
    p = path or default_config_path()
    if not p.exists():
        return AppConfig()
    data = json.loads(p.read_text(encoding="utf-8"))
    return AppConfig(
        enabled_categories=set(data.get("enabled_categories", list(ALL_CATEGORIES))),
        custom_dictionary=dict(data.get("custom_dictionary", {})),
        output_dir=data.get("output_dir"),
        skip_preview=bool(data.get("skip_preview", False)),
        mask_mode=data.get("mask_mode", "token"),
        action_mode=data.get("action_mode", "auto"),
        advanced_expanded=bool(data.get("advanced_expanded", False)),
    )


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "enabled_categories": sorted(cfg.enabled_categories),
        "custom_dictionary": cfg.custom_dictionary,
        "output_dir": cfg.output_dir,
        "skip_preview": cfg.skip_preview,
        "mask_mode": cfg.mask_mode,
        "action_mode": cfg.action_mode,
        "advanced_expanded": cfg.advanced_expanded,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_new_dictionary_entries(
        existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """`existing` に無い語句だけを `new` から抜き出して返す（既存優先）。

    プレビューで手動追加した検出のカスタム辞書への自動登録に使う。既存
    エントリを上書きすると、ユーザーが設定画面で意図して割り当てた種別が
    ドラッグ追加の既定カテゴリ（CUSTOM）で黙って塗り替わるため、追加のみ。
    """
    return {w: c for w, c in new.items() if w not in existing}


def import_dictionary_csv(path: Path) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    warnings: list[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != ["語句", "種別"]:
            raise ValueError("辞書CSVのヘッダーは「語句,種別」である必要があります")
        for row in reader:
            if not row or not row[0]:
                continue
            word = row[0]
            category = LABEL_TO_CATEGORY.get(row[1] if len(row) > 1 else "", "CUSTOM")
            if word in result:
                warnings.append(f"重複語句をスキップしました: {word}")
                continue
            result[word] = category
    return result, warnings


def export_dictionary_csv(d: dict[str, str], path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["語句", "種別"])
        for word, category in d.items():
            writer.writerow([word, CATEGORY_LABELS.get(category, category)])
