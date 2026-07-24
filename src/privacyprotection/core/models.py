"""コア層の共通データ型。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# カテゴリ → トークンの日本語ラベル（設計書 4.4 の固定語彙）
CATEGORY_LABELS: dict[str, str] = {
    "PERSON": "人名",
    "ORG": "組織",
    "LOC": "地名",
    "PHONE": "電話",
    "EMAIL": "メール",
    "ADDRESS": "住所",
    "POSTAL": "番号",
    "MYNUMBER": "番号",
    "CREDITCARD": "番号",
    "CUSTOM": "カスタム",
}

_LABELS_ALT = "|".join(sorted(set(CATEGORY_LABELS.values())))
TOKEN_RE = re.compile(rf"【(?:{_LABELS_ALT})_\d+】")


def _build_label_to_category() -> dict[str, str]:
    """ラベル→代表カテゴリの逆引きを構築する（"先勝ち": 複数カテゴリが同じ
    ラベルを共有する場合、CATEGORY_LABELS の宣言順で最初に出てきたものを
    代表として扱う）。"""
    mapping: dict[str, str] = {}
    for category, label in CATEGORY_LABELS.items():
        mapping.setdefault(label, category)
    return mapping


# ラベル→代表カテゴリの逆引き（"先勝ち"、上記参照）。以前は config.py・
# core/mapping_io.py・gui/settings_dialog.py・gui/preview_dialog.py の
# 4箇所でこの同じロジックが個別に複製されていた（最終レビュー Finding 6）。
# ラベル語彙の唯一の正典である CATEGORY_LABELS の隣に一本化し、各呼び出し元
# はここから import して使う。
LABEL_TO_CATEGORY: dict[str, str] = _build_label_to_category()


@dataclass
class Detection:
    text: str
    category: str
    start: int
    end: int
    source: str  # "pattern" | "dictionary" | "ner"
    enabled: bool = True


@dataclass
class Fragment:
    text: str
    location: str  # 例: "Sheet1!A1", "para:3", "line:10"
    # テキスト系ファイルの元エンコーディング（handlers/text_handler.py が
    # read_fragments で設定し、write_fragments が書き戻し時に使う）。
    # Office系ハンドラは使わないため既定値はNoneのまま。
    # 以前はFragmentに未宣言の動的属性として`.encoding`を生やしていた
    # （Task 9以降、`Fragment`に`slots=True`が付いていないことにのみ依存する
    # 潜在的な不具合の種だった）が、正式なフィールドとして宣言した
    # （最終レビュー「推奨」対応）。
    encoding: str | None = None


@dataclass
class MappingEntry:
    token: str
    original: str
    category: str
    count: int


@dataclass
class MappingTable:
    entries: list[MappingEntry] = field(default_factory=list)

    def token_for(self, original: str) -> str | None:
        for e in self.entries:
            if e.original == original:
                return e.token
        return None

    def original_for(self, token: str) -> str | None:
        for e in self.entries:
            if e.token == token:
                return e.original
        return None


@dataclass
class RestoreResult:
    text: str
    unknown_tokens: list[str] = field(default_factory=list)
