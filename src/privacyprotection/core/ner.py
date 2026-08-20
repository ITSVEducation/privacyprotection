"""GiNZAによる固有表現抽出。モデルは遅延ロードし、プロセス内で共有する。"""
from __future__ import annotations

import threading

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


# Pipeline は実行のたびに新しい NerDetector を組み立てる（Pipeline.from_config）
# ため、モデルをインスタンス保持にすると毎回 spacy.load（数秒〜十数秒）が走る。
# プロセス内で1回だけロードして全インスタンスで共有する。ロックは GUI スレッド
# とワーカースレッドからの同時初回呼び出しによる二重ロード防止。
_NLP = None
_NLP_LOCK = threading.Lock()


def _load_model():
    global _NLP
    if _NLP is None:
        with _NLP_LOCK:
            if _NLP is None:
                import spacy
                _NLP = spacy.load("ja_ginza")
    return _NLP


class NerDetector:
    def detect(self, text: str) -> list[Detection]:
        if not text.strip():
            return []
        doc = _load_model()(text)
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
