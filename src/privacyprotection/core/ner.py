"""GiNZAによる固有表現抽出。モデルは遅延ロード（起動高速化）。"""
from __future__ import annotations

from .models import Detection

# GiNZA(関根拡張ENE)のラベル → 本アプリのカテゴリ
_LABEL_MAP = {
    "Person": "PERSON",
    "ORG": "ORG",
    "Company": "ORG",
    "Corporation_Other": "ORG",
    "Government": "ORG",
    "Political_Organization": "ORG",
    "Organization_Other": "ORG",
    "School": "ORG",
    "GPE": "LOC",
    "City": "LOC",
    "Province": "LOC",
    "Country": "LOC",
    "Location_Other": "LOC",
}


class NerDetector:
    def __init__(self):
        self._nlp = None

    def _load(self):
        if self._nlp is None:
            import spacy
            self._nlp = spacy.load("ja_ginza")
        return self._nlp

    def detect(self, text: str) -> list[Detection]:
        if not text.strip():
            return []
        doc = self._load()(text)
        results: list[Detection] = []
        for ent in doc.ents:
            category = _LABEL_MAP.get(ent.label_)
            if category is None:
                continue
            results.append(Detection(
                text=ent.text, category=category,
                start=ent.start_char, end=ent.end_char, source="ner",
            ))
        return results
