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
