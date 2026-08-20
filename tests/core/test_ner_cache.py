"""core/ner.py のモデルキャッシュのテスト（実モデル不要）。

Pipeline は実行のたびに新しい NerDetector を組み立てるため、モデルを
インスタンスごとに持つと毎回 spacy.load（数秒〜十数秒）が走り、GUI が
実行のたびに長時間フリーズする。プロセス内で1回だけロードされることを固定する。
"""
import sys
import types

from privacyprotection.core import ner


def _fake_spacy(calls: list):
    def fake_load(name):
        calls.append(name)
        return lambda text: types.SimpleNamespace(ents=[])
    return types.SimpleNamespace(load=fake_load)


def test_model_loaded_once_across_instances(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ner, "_NLP", None)  # キャッシュを空にして開始
    monkeypatch.setitem(sys.modules, "spacy", _fake_spacy(calls))

    ner.NerDetector().detect("山田太郎")
    ner.NerDetector().detect("株式会社サンプル")

    assert calls == ["ja_ginza"]


def test_empty_text_does_not_trigger_load(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ner, "_NLP", None)
    monkeypatch.setitem(sys.modules, "spacy", _fake_spacy(calls))

    assert ner.NerDetector().detect("   ") == []
    assert calls == []
