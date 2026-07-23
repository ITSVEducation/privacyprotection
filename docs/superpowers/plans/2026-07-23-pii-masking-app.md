# 個人情報マスキングアプリ 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完全ローカルで動作するWindows向け個人情報マスキングGUIアプリ（検出→プレビュー→マスク→復元、テキスト/Office/クリップボード/フォルダ一括対応）を構築する。

**Architecture:** 3層構成。コア層（検出・置換・復元、純Python・GUI非依存）、ファイルI/O層（Handler共通インターフェース）、GUI層（PySide6）。コア層はすべてpytestでTDD、GUIは手動確認中心。

**Tech Stack:** Python 3.11+ / PySide6 / GiNZA (spaCy + ja_ginza) / openpyxl / python-docx / python-pptx / pytest / PyInstaller

**Spec:** `docs/superpowers/specs/2026-07-23-pii-masking-app-design.md`（以下「設計書」）

## Global Constraints

- Python 3.11以上。パッケージ名は `privacyprotection`、srcレイアウト（`src/privacyprotection/`）
- **ネットワーク通信を行うコードを一切書かない**（`socket` / `urllib.request` / `requests` 等の使用禁止。NERモデルは同梱）
- **元ファイルは決して変更しない**。出力は常に別ファイル（`_masked` 付き）、同名時は連番 `_masked(2)`
- 出力は一時ファイルに書いてからリネームで確定。対応表をマスク済ファイルより**先に**確定する
- トークン文法は `【<種別ラベル>_<連番>】`。種別ラベルは固定語彙: 人名/組織/地名/電話/メール/住所/番号/カスタム
- 対応表は UTF-8 BOM付きCSV、ヘッダー `トークン,元の値,種別,出現回数`、拡張子 `.pmap.csv`
- テキスト出力は元のエンコーディング・改行コードを維持。Unicode正規化はしない
- エラーメッセージ・ログに検出された値そのものを記録しない
- コミットメッセージは Conventional Commits（`feat:` / `test:` / `chore:`）

## ファイル構成（最終形）

```
pyproject.toml
src/privacyprotection/
  __init__.py
  core/
    __init__.py
    models.py        # Detection, Fragment, MappingEntry, MappingTable, RestoreResult, カテゴリ定義
    patterns.py      # PatternDetector（正規表現PII検出）
    dictionary.py    # DictionaryDetector（カスタム辞書）
    ner.py           # NerDetector（GiNZA）
    detector.py      # Detector（3検出器のマージ＋重複解決）
    masker.py        # Masker（token/redact、衝突対策）
    mapping_io.py    # 対応表CSVの読み書き
    restorer.py      # Restorer（完全一致逆置換）
  handlers/
    __init__.py
    base.py          # FileHandler ABC
    text_handler.py  # テキスト系（エンコーディング判定・保持）
    xlsx_handler.py
    docx_handler.py
    pptx_handler.py
    registry.py      # 拡張子→Handler解決
    folder_walker.py # 再帰列挙（境界条件処理）
  services/
    __init__.py
    report.py        # FileReport / BatchReport / _report.txt生成
    pipeline.py      # mask_file / mask_folder / restore_text / restore_file（原子的出力）
  config.py          # 設定・カスタム辞書の %APPDATA% 保存
  gui/
    __init__.py
    app.py           # エントリポイント
    main_window.py   # メイン画面（D&D、モード切替、クリップボード）
    worker.py        # QThreadワーカー
    preview_dialog.py
    report_dialog.py
    settings_dialog.py
tests/
  core/    test_models.py test_patterns.py test_dictionary.py test_detector.py
           test_ner.py test_masker.py test_mapping_io.py test_restorer.py
  handlers/ test_text_handler.py test_xlsx_handler.py test_docx_handler.py
            test_pptx_handler.py test_folder_walker.py
  services/ test_report.py test_pipeline.py
  test_no_network.py
scripts/
  build.ps1          # PyInstallerビルド
```

---

### Task 1: プロジェクト土台とコアモデル

**Files:**
- Create: `pyproject.toml`, `src/privacyprotection/__init__.py`, `src/privacyprotection/core/__init__.py`, `src/privacyprotection/core/models.py`
- Test: `tests/core/test_models.py`

**Interfaces:**
- Produces: `Detection(text, category, start, end, source, enabled=True)`、`Fragment(text, location)`、`MappingEntry(token, original, category, count)`、`MappingTable`（`entries: list[MappingEntry]`、`token_for(original) -> str | None`、`original_for(token) -> str | None`）、`RestoreResult(text, unknown_tokens)`、定数 `CATEGORY_LABELS: dict[str, str]`（カテゴリ→日本語トークンラベル）、`TOKEN_RE`（コンパイル済み正規表現）

- [ ] **Step 1: pyproject.toml とパッケージ骨格を作成**

```toml
# pyproject.toml
[project]
name = "privacyprotection"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "PySide6>=6.6",
    "ginza>=5.2",
    "ja_ginza>=5.2",
    "openpyxl>=3.1",
    "python-docx>=1.1",
    "python-pptx>=0.6.23",
    "charset-normalizer>=3.3",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pyinstaller>=6.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/privacyprotection/__init__.py` と `src/privacyprotection/core/__init__.py` は空ファイルで作成。
仮想環境を作成し依存をインストール: `python -m venv .venv; .venv\Scripts\pip install -e .[dev]`

- [ ] **Step 2: 失敗するテストを書く**

```python
# tests/core/test_models.py
from privacyprotection.core.models import (
    CATEGORY_LABELS, TOKEN_RE, Detection, Fragment,
    MappingEntry, MappingTable, RestoreResult,
)

def test_detection_defaults():
    d = Detection(text="山田太郎", category="PERSON", start=0, end=4, source="ner")
    assert d.enabled is True

def test_category_labels_cover_all_categories():
    assert CATEGORY_LABELS["PERSON"] == "人名"
    assert CATEGORY_LABELS["ORG"] == "組織"
    assert CATEGORY_LABELS["LOC"] == "地名"
    assert CATEGORY_LABELS["PHONE"] == "電話"
    assert CATEGORY_LABELS["EMAIL"] == "メール"
    assert CATEGORY_LABELS["ADDRESS"] == "住所"
    assert CATEGORY_LABELS["POSTAL"] == "番号"
    assert CATEGORY_LABELS["MYNUMBER"] == "番号"
    assert CATEGORY_LABELS["CREDITCARD"] == "番号"
    assert CATEGORY_LABELS["CUSTOM"] == "カスタム"

def test_token_re_matches_token_grammar():
    assert TOKEN_RE.fullmatch("【人名_1】")
    assert TOKEN_RE.fullmatch("【カスタム_42】")
    assert not TOKEN_RE.fullmatch("【人名1】")
    assert not TOKEN_RE.fullmatch("【未知種別_1】")

def test_mapping_table_lookup():
    t = MappingTable(entries=[MappingEntry("【人名_1】", "山田太郎", "PERSON", 5)])
    assert t.token_for("山田太郎") == "【人名_1】"
    assert t.original_for("【人名_1】") == "山田太郎"
    assert t.token_for("不明") is None
    assert t.original_for("【人名_9】") is None
```

- [ ] **Step 3: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_models.py -v`
Expected: FAIL（ModuleNotFoundError: privacyprotection.core.models）

- [ ] **Step 4: models.py を実装**

```python
# src/privacyprotection/core/models.py
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
```

- [ ] **Step 5: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_models.py -v`
Expected: PASS（4件）

- [ ] **Step 6: .gitignore を追加してコミット**

`.gitignore` に `.venv/`, `__pycache__/`, `dist/`, `build/`, `*.spec` を記載。

```bash
git add -A
git commit -m "feat: add project scaffold and core data models"
```

---

### Task 2: PatternDetector（正規表現PII検出）

**Files:**
- Create: `src/privacyprotection/core/patterns.py`
- Test: `tests/core/test_patterns.py`

**Interfaces:**
- Consumes: `Detection`（Task 1）
- Produces: `PatternDetector().detect(text: str) -> list[Detection]`（source="pattern"、返却は start 昇順）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_patterns.py
from privacyprotection.core.patterns import PatternDetector

det = PatternDetector()

def cats(text):
    return [(d.text, d.category) for d in det.detect(text)]

def test_phone_hyphenated_and_plain():
    assert ("03-1234-5678", "PHONE") in cats("電話は03-1234-5678です")
    assert ("09012345678", "PHONE") in cats("携帯09012345678まで")

def test_email():
    assert ("yamada@example.co.jp", "EMAIL") in cats("送付先: yamada@example.co.jp")

def test_postal_code():
    assert ("〒100-0001", "POSTAL") in cats("〒100-0001 千代田区")
    assert ("100-0001", "POSTAL") in cats("郵便番号は100-0001")

def test_mynumber_12_digits():
    assert ("1234 5678 9012", "MYNUMBER") in cats("マイナンバー: 1234 5678 9012")

def test_creditcard_16_digits():
    assert ("1234-5678-9012-3456", "CREDITCARD") in cats("カード番号 1234-5678-9012-3456")

def test_address():
    found = cats("住所は東京都千代田区丸の内1-1-1です")
    assert any(c == "ADDRESS" and t.startswith("東京都") for t, c in found)

def test_no_false_positive_on_plain_text():
    assert cats("これは普通の文章です。") == []

def test_digits_inside_longer_number_not_matched():
    # 20桁の連番はクレカ(16桁)として部分マッチしない
    assert all(c != "CREDITCARD" for _, c in cats("12345678901234567890"))

def test_results_sorted_by_start():
    r = det.detect("a@b.jp と 03-1234-5678")
    assert [d.start for d in r] == sorted(d.start for d in r)
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_patterns.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: patterns.py を実装**

```python
# src/privacyprotection/core/patterns.py
"""定型PIIの正規表現検出。"""
from __future__ import annotations

import re

from .models import Detection

# 順序に意味あり: 長い形式（クレカ16桁）をマイナンバー(12桁)より先に評価
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CREDITCARD", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("MYNUMBER", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}(?!\d)(?![- ]\d)")),
    ("PHONE", re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)|(?<!\d)0\d{9,10}(?!\d)")),
    ("POSTAL", re.compile(r"〒\s?\d{3}-\d{4}|(?<!\d)\d{3}-\d{4}(?!\d)")),
    ("ADDRESS", re.compile(
        r"(?:北海道|東京都|京都府|大阪府|[一-龠]{2,3}県)"
        r"[一-龠ぁ-んァ-ヶa-zA-Z0-9０-９\-ー−]{3,30}"
    )),
]


class PatternDetector:
    def detect(self, text: str) -> list[Detection]:
        results: list[Detection] = []
        taken: list[tuple[int, int]] = []
        for category, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                # 先に確定した検出と重なる範囲はスキップ（例: 郵便番号がクレカの一部にマッチ等）
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                taken.append(span)
                results.append(Detection(
                    text=m.group(), category=category,
                    start=m.start(), end=m.end(), source="pattern",
                ))
        results.sort(key=lambda d: d.start)
        return results
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_patterns.py -v`
Expected: PASS（10件）。落ちる場合は正規表現を調整（テストが仕様）。

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/patterns.py tests/core/test_patterns.py
git commit -m "feat: add regex-based PII pattern detector"
```

---

### Task 3: DictionaryDetector（カスタム辞書）

**Files:**
- Create: `src/privacyprotection/core/dictionary.py`
- Test: `tests/core/test_dictionary.py`

**Interfaces:**
- Consumes: `Detection`（Task 1）
- Produces: `DictionaryDetector(entries: dict[str, str])`（語句→カテゴリ）、`.detect(text) -> list[Detection]`（source="dictionary"）。長い語句を優先し、同一位置での重複登録語の二重検出をしない

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_dictionary.py
from privacyprotection.core.dictionary import DictionaryDetector

def test_detects_registered_words():
    det = DictionaryDetector({"株式会社サンプル": "CUSTOM", "PRJ-001": "CUSTOM"})
    r = det.detect("株式会社サンプルのPRJ-001について")
    assert [(d.text, d.category, d.source) for d in r] == [
        ("株式会社サンプル", "CUSTOM", "dictionary"),
        ("PRJ-001", "CUSTOM", "dictionary"),
    ]

def test_longer_entry_wins_over_substring():
    det = DictionaryDetector({"山田": "PERSON", "山田製作所": "ORG"})
    r = det.detect("山田製作所に連絡")
    assert [(d.text, d.category) for d in r] == [("山田製作所", "ORG")]

def test_multiple_occurrences_all_detected():
    det = DictionaryDetector({"山田": "PERSON"})
    assert len(det.detect("山田と山田")) == 2

def test_empty_dictionary():
    assert DictionaryDetector({}).detect("なにか") == []
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_dictionary.py -v`
Expected: FAIL

- [ ] **Step 3: dictionary.py を実装**

```python
# src/privacyprotection/core/dictionary.py
"""カスタム辞書による検出。"""
from __future__ import annotations

import re

from .models import Detection


class DictionaryDetector:
    def __init__(self, entries: dict[str, str]):
        self._entries = entries
        if entries:
            # 長い語句を優先（正規表現の選択肢は先勝ちのため長い順に並べる）
            alternation = "|".join(
                re.escape(w) for w in sorted(entries, key=len, reverse=True)
            )
            self._re: re.Pattern | None = re.compile(alternation)
        else:
            self._re = None

    def detect(self, text: str) -> list[Detection]:
        if self._re is None:
            return []
        return [
            Detection(
                text=m.group(), category=self._entries[m.group()],
                start=m.start(), end=m.end(), source="dictionary",
            )
            for m in self._re.finditer(text)
        ]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_dictionary.py -v`
Expected: PASS（4件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/dictionary.py tests/core/test_dictionary.py
git commit -m "feat: add custom dictionary detector"
```

---

### Task 4: Detector（マージ＋重複解決）

**Files:**
- Create: `src/privacyprotection/core/detector.py`
- Test: `tests/core/test_detector.py`

**Interfaces:**
- Consumes: `Detection`、`PatternDetector`、`DictionaryDetector`（Task 1-3）
- Produces: `Detector(detectors: list, enabled_categories: set[str] | None = None)`、`.detect(text) -> list[Detection]`。重複解決は設計書4.2: ①長い範囲優先 ②同長なら dictionary > pattern > ner ③残り部分は非重複なら採用。`enabled_categories` 指定時はそれ以外のカテゴリを除外（None は全カテゴリ有効）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_detector.py
from privacyprotection.core.detector import Detector
from privacyprotection.core.models import Detection


class FakeDetector:
    def __init__(self, results):
        self._results = results
    def detect(self, text):
        return list(self._results)


def d(text, cat, start, source):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source=source)


def test_longer_span_wins():
    ner = FakeDetector([d("山田", "PERSON", 4, "ner")])
    dic = FakeDetector([d("株式会社山田製作所", "ORG", 0, "dictionary")])
    r = Detector([dic, ner]).detect("株式会社山田製作所")
    assert [(x.text, x.category) for x in r] == [("株式会社山田製作所", "ORG")]

def test_same_span_priority_dictionary_over_ner():
    ner = FakeDetector([d("山田太郎", "PERSON", 0, "ner")])
    dic = FakeDetector([d("山田太郎", "CUSTOM", 0, "dictionary")])
    r = Detector([ner, dic]).detect("山田太郎")
    assert [(x.source,) for x in r] == [("dictionary",)]

def test_non_overlapping_all_kept_sorted():
    a = FakeDetector([d("bbb", "ORG", 10, "ner")])
    b = FakeDetector([d("aaa", "PERSON", 0, "ner")])
    r = Detector([a, b]).detect("x" * 20)
    assert [x.start for x in r] == [0, 10]

def test_partial_overlap_earlier_start_wins():
    a = FakeDetector([d("abcd", "PERSON", 0, "ner")])
    b = FakeDetector([d("cdef", "ORG", 2, "ner")])
    r = Detector([a, b]).detect("abcdef")
    assert [(x.text,) for x in r] == [("abcd",)]

def test_disabled_category_filtered():
    a = FakeDetector([d("東京", "LOC", 0, "ner"), d("山田", "PERSON", 5, "ner")])
    r = Detector([a], enabled_categories={"PERSON"}).detect("東京…山田")
    assert [(x.category,) for x in r] == [("PERSON",)]
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_detector.py -v`
Expected: FAIL

- [ ] **Step 3: detector.py を実装**

```python
# src/privacyprotection/core/detector.py
"""検出器のマージと重複解決（設計書 4.2）。"""
from __future__ import annotations

from .models import Detection

_SOURCE_PRIORITY = {"dictionary": 0, "pattern": 1, "ner": 2}


class Detector:
    def __init__(self, detectors: list, enabled_categories: set[str] | None = None):
        self._detectors = detectors
        self._enabled = enabled_categories

    def detect(self, text: str) -> list[Detection]:
        candidates: list[Detection] = []
        for det in self._detectors:
            candidates.extend(det.detect(text))
        if self._enabled is not None:
            candidates = [c for c in candidates if c.category in self._enabled]
        # 長い範囲優先 → ソース優先度 → 開始位置の順で採用候補を並べる
        candidates.sort(key=lambda c: (-(c.end - c.start),
                                       _SOURCE_PRIORITY.get(c.source, 9),
                                       c.start))
        accepted: list[Detection] = []
        for c in candidates:
            if any(a.start < c.end and c.start < a.end for a in accepted):
                continue
            accepted.append(c)
        # 部分重複の同長ペアは「開始位置が先」を優先させる
        accepted.sort(key=lambda c: c.start)
        return accepted
```

注意: `test_partial_overlap_earlier_start_wins` は同じ長さの部分重複で開始位置が先の方が残ることを要求する。上記実装ではソート第3キー（c.start）で先に採用されるため満たされる。

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_detector.py -v`
Expected: PASS（5件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/detector.py tests/core/test_detector.py
git commit -m "feat: add detector merge with overlap resolution"
```

---

### Task 5: NerDetector（GiNZA）

**Files:**
- Create: `src/privacyprotection/core/ner.py`
- Test: `tests/core/test_ner.py`

**Interfaces:**
- Consumes: `Detection`（Task 1）
- Produces: `NerDetector()`（初回 `detect` 呼び出し時にモデルを遅延ロード）、`.detect(text) -> list[Detection]`（source="ner"、category は PERSON/ORG/LOC のみ。その他のエンティティは無視）

- [ ] **Step 1: 失敗するテストを書く**

設計書8: NERは精度テストせず結合レベル確認のみ。モデル未導入環境ではスキップする。

```python
# tests/core/test_ner.py
import pytest

spacy = pytest.importorskip("spacy")
try:
    spacy.load("ja_ginza")
except OSError:
    pytest.skip("ja_ginza model not installed", allow_module_level=True)

from privacyprotection.core.ner import NerDetector

def test_detects_person_and_org_as_detection_objects():
    det = NerDetector()
    r = det.detect("山田太郎は株式会社サンプルに勤務している。")
    assert all(d.source == "ner" for d in r)
    assert all(d.category in {"PERSON", "ORG", "LOC"} for d in r)
    assert any(d.category == "PERSON" for d in r)
    # start/end がテキストと整合していること
    for d in r:
        assert "山田太郎は株式会社サンプルに勤務している。"[d.start:d.end] == d.text

def test_plain_text_returns_list():
    assert isinstance(NerDetector().detect("12345"), list)
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_ner.py -v`
Expected: FAIL（ModuleNotFoundError: privacyprotection.core.ner）。※ ja_ginza 未導入なら SKIP になるので、その場合は `pip install ginza ja_ginza` を先に実行

- [ ] **Step 3: ner.py を実装**

```python
# src/privacyprotection/core/ner.py
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_ner.py -v`
Expected: PASS（2件）。GiNZAのラベル体系がインストール版と異なり PERSON が出ない場合は、実際の `ent.label_` を `print` で確認し `_LABEL_MAP` に追記する（テストが仕様）。

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/ner.py tests/core/test_ner.py
git commit -m "feat: add GiNZA-based NER detector with lazy model load"
```

---

### Task 6: Masker（トークン置換・塗りつぶし・衝突対策）

**Files:**
- Create: `src/privacyprotection/core/masker.py`
- Test: `tests/core/test_masker.py`

**Interfaces:**
- Consumes: `Detection`, `MappingTable`, `MappingEntry`, `CATEGORY_LABELS`, `TOKEN_RE`（Task 1）
- Produces: `Masker(mode: str)`（"token" | "redact"）。`mask_fragments(fragments: list[str], detections: list[list[Detection]]) -> tuple[list[str], MappingTable]`。**Maskerインスタンスは処理単位（フォルダ一括全体）で使い回すと同一値→同一トークンが維持される**。`scan_existing_tokens(fragments) -> int`（既存トークンの最大連番を返し、内部カウンタをその後ろから開始）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_masker.py
from privacyprotection.core.masker import Masker
from privacyprotection.core.models import Detection


def det(text, cat, start):
    return Detection(text=text, category=cat, start=start,
                     end=start + len(text), source="ner")


def test_token_mode_replaces_and_builds_mapping():
    m = Masker(mode="token")
    frags = ["山田太郎です。山田太郎に連絡。"]
    ds = [[det("山田太郎", "PERSON", 0), det("山田太郎", "PERSON", 7)]]
    out, table = m.mask_fragments(frags, ds)
    assert out == ["【人名_1】です。【人名_1】に連絡。"]
    assert len(table.entries) == 1
    assert table.entries[0].count == 2

def test_token_numbering_per_label_and_cross_fragment_consistency():
    m = Masker(mode="token")
    out, table = m.mask_fragments(
        ["山田太郎と佐藤花子", "山田太郎", "株式会社A"],
        [[det("山田太郎", "PERSON", 0), det("佐藤花子", "PERSON", 5)],
         [det("山田太郎", "PERSON", 0)],
         [det("株式会社A", "ORG", 0)]],
    )
    assert out == ["【人名_1】と【人名_2】", "【人名_1】", "【組織_1】"]

def test_disabled_detection_not_masked():
    m = Masker(mode="token")
    d1 = det("山田太郎", "PERSON", 0)
    d1.enabled = False
    out, table = m.mask_fragments(["山田太郎"], [[d1]])
    assert out == ["山田太郎"]
    assert table.entries == []

def test_redact_mode_no_mapping():
    m = Masker(mode="redact")
    out, table = m.mask_fragments(["山田太郎です"], [[det("山田太郎", "PERSON", 0)]])
    assert out == ["●●●●です"]
    assert table.entries == []

def test_collision_scan_shifts_counter():
    m = Masker(mode="token")
    max_n = m.scan_existing_tokens(["既に【人名_3】がある文書"])
    assert max_n == 3
    out, _ = m.mask_fragments(["山田太郎"], [[det("山田太郎", "PERSON", 0)]])
    assert out == ["【人名_4】"]

def test_same_value_shared_across_number_labels():
    # POSTAL と MYNUMBER は同じ「番号」ラベルだがカウンタは共有される
    m = Masker(mode="token")
    out, _ = m.mask_fragments(
        ["100-0001 と 1234 5678 9012"],
        [[det("100-0001", "POSTAL", 0), det("1234 5678 9012", "MYNUMBER", 11)]],
    )
    assert out == ["【番号_1】 と 【番号_2】"]
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_masker.py -v`
Expected: FAIL

- [ ] **Step 3: masker.py を実装**

```python
# src/privacyprotection/core/masker.py
"""置換エンジン（設計書 4.4）。"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import CATEGORY_LABELS, TOKEN_RE, Detection, MappingEntry, MappingTable

_REDACT = "●●●●"
_TOKEN_NUM_RE = re.compile(r"【(?:[^【】_]+)_(\d+)】")


class Masker:
    def __init__(self, mode: str):
        if mode not in ("token", "redact"):
            raise ValueError(f"unknown mode: {mode}")
        self._mode = mode
        self._counters: dict[str, int] = defaultdict(int)  # ラベル→次の連番-1
        self._value_tokens: dict[str, MappingEntry] = {}   # 元の値→エントリ

    def scan_existing_tokens(self, fragments: list[str]) -> int:
        """原文中の既存トークン形式を検出し、連番をその後ろから開始する。"""
        max_n = 0
        for frag in fragments:
            for m in TOKEN_RE.finditer(frag):
                num = int(_TOKEN_NUM_RE.fullmatch(m.group()).group(1))
                max_n = max(max_n, num)
        if max_n:
            for label in set(CATEGORY_LABELS.values()):
                self._counters[label] = max(self._counters[label], max_n)
        return max_n

    def _token_for(self, detection: Detection) -> str:
        entry = self._value_tokens.get(detection.text)
        if entry is None:
            label = CATEGORY_LABELS[detection.category]
            self._counters[label] += 1
            entry = MappingEntry(
                token=f"【{label}_{self._counters[label]}】",
                original=detection.text,
                category=detection.category,
                count=0,
            )
            self._value_tokens[detection.text] = entry
        return entry.token

    def mask_fragments(
        self, fragments: list[str], detections: list[list[Detection]]
    ) -> tuple[list[str], MappingTable]:
        out: list[str] = []
        for frag, dets in zip(fragments, detections):
            active = sorted(
                (d for d in dets if d.enabled), key=lambda d: d.start, reverse=True
            )
            text = frag
            for d in active:  # 後ろから置換して位置ずれを防ぐ
                if self._mode == "token":
                    replacement = self._token_for(d)
                    self._value_tokens[d.text].count += 1
                else:
                    replacement = _REDACT
                text = text[:d.start] + replacement + text[d.end:]
            out.append(text)
        table = MappingTable(entries=list(self._value_tokens.values()))
        return out, table
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_masker.py -v`
Expected: PASS（6件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/masker.py tests/core/test_masker.py
git commit -m "feat: add masker with token/redact modes and collision handling"
```

---

### Task 7: 対応表CSVの読み書き

**Files:**
- Create: `src/privacyprotection/core/mapping_io.py`
- Test: `tests/core/test_mapping_io.py`

**Interfaces:**
- Consumes: `MappingTable`, `MappingEntry`（Task 1）
- Produces: `write_mapping(table: MappingTable, path: Path) -> None`（UTF-8 BOM、ヘッダー `トークン,元の値,種別,出現回数`、種別列は日本語ラベル）、`read_mapping(path: Path) -> MappingTable`（ヘッダー検証、`ValueError` on 不正ヘッダー）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_mapping_io.py
import pytest
from privacyprotection.core.mapping_io import read_mapping, write_mapping
from privacyprotection.core.models import MappingEntry, MappingTable


def test_roundtrip(tmp_path):
    table = MappingTable(entries=[
        MappingEntry("【人名_1】", "山田太郎", "PERSON", 5),
        MappingEntry("【組織_1】", 'カンマ,と"引用符"入り', "ORG", 1),
        MappingEntry("【カスタム_1】", "改行\n入り", "CUSTOM", 2),
    ])
    p = tmp_path / "out.pmap.csv"
    write_mapping(table, p)
    loaded = read_mapping(p)
    assert [(e.token, e.original, e.count) for e in loaded.entries] == \
           [(e.token, e.original, e.count) for e in table.entries]

def test_file_has_bom_and_japanese_header(tmp_path):
    p = tmp_path / "out.pmap.csv"
    write_mapping(MappingTable(), p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0] == "トークン,元の値,種別,出現回数"

def test_read_rejects_wrong_header(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b,c,d\r\nx,y,z,1\r\n", encoding="utf-8-sig")
    with pytest.raises(ValueError):
        read_mapping(p)
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_mapping_io.py -v`
Expected: FAIL

- [ ] **Step 3: mapping_io.py を実装**

```python
# src/privacyprotection/core/mapping_io.py
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_mapping_io.py -v`
Expected: PASS（3件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/mapping_io.py tests/core/test_mapping_io.py
git commit -m "feat: add human-readable CSV mapping table I/O"
```

---

### Task 8: Restorer（完全一致逆置換）

**Files:**
- Create: `src/privacyprotection/core/restorer.py`
- Test: `tests/core/test_restorer.py`

**Interfaces:**
- Consumes: `MappingTable`, `RestoreResult`, `TOKEN_RE`（Task 1）
- Produces: `Restorer(mapping: MappingTable)`、`.restore(text: str) -> RestoreResult`。対応表にあるトークンは逆置換、テキスト中に残った（対応表にない）トークン形式文字列は `unknown_tokens` に列挙して本文には残す

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_restorer.py
from privacyprotection.core.models import MappingEntry, MappingTable
from privacyprotection.core.restorer import Restorer

TABLE = MappingTable(entries=[
    MappingEntry("【人名_1】", "山田太郎", "PERSON", 2),
    MappingEntry("【人名_2】", "佐藤花子", "PERSON", 1),
])

def test_restores_known_tokens():
    r = Restorer(TABLE).restore("【人名_1】と【人名_2】が【人名_1】に")
    assert r.text == "山田太郎と佐藤花子が山田太郎に"
    assert r.unknown_tokens == []

def test_unknown_token_left_and_reported():
    r = Restorer(TABLE).restore("【人名_1】と【人名_9】")
    assert r.text == "山田太郎と【人名_9】"
    assert r.unknown_tokens == ["【人名_9】"]

def test_altered_token_not_fuzzy_matched():
    # AIが改変したトークン（区切り欠落）は復元されない
    r = Restorer(TABLE).restore("【人名1】のままです")
    assert r.text == "【人名1】のままです"
    assert r.unknown_tokens == []  # トークン文法に一致しないので unknown 扱いもしない

def test_mask_restore_roundtrip():
    from privacyprotection.core.masker import Masker
    from privacyprotection.core.models import Detection
    original = "担当は山田太郎（電話03-1234-5678）です。"
    m = Masker(mode="token")
    ds = [Detection("山田太郎", "PERSON", 3, 7, "ner"),
          Detection("03-1234-5678", "PHONE", 10, 22, "pattern")]
    masked, table = m.mask_fragments([original], [ds])
    restored = Restorer(table).restore(masked[0])
    assert restored.text == original
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_restorer.py -v`
Expected: FAIL

- [ ] **Step 3: restorer.py を実装**

```python
# src/privacyprotection/core/restorer.py
"""復元エンジン（設計書 5.2）。完全一致のみ、曖昧マッチはしない。"""
from __future__ import annotations

import re

from .models import TOKEN_RE, MappingTable, RestoreResult


class Restorer:
    def __init__(self, mapping: MappingTable):
        self._map = {e.token: e.original for e in mapping.entries}

    def restore(self, text: str) -> RestoreResult:
        unknown: list[str] = []

        def _sub(m: re.Match) -> str:
            token = m.group()
            if token in self._map:
                return self._map[token]
            if token not in unknown:
                unknown.append(token)
            return token

        return RestoreResult(text=TOKEN_RE.sub(_sub, text), unknown_tokens=unknown)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/core/test_restorer.py -v`
Expected: PASS（4件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/restorer.py tests/core/test_restorer.py
git commit -m "feat: add exact-match restorer with unknown token reporting"
```

---

### Task 9: FileHandler基底とTextHandler（エンコーディング保持）

**Files:**
- Create: `src/privacyprotection/handlers/__init__.py`, `src/privacyprotection/handlers/base.py`, `src/privacyprotection/handlers/text_handler.py`
- Test: `tests/handlers/test_text_handler.py`

**Interfaces:**
- Consumes: `Fragment`（Task 1）
- Produces:
  - `FileHandler`（ABC）: `extensions: ClassVar[list[str]]`、`read_fragments(path: Path) -> list[Fragment]`、`write_fragments(src: Path, dst: Path, masked: list[Fragment]) -> None`
  - `TextHandler`: extensions = [".txt", ".csv", ".json", ".md", ".log", ".xml", ".html", ".yaml", ".yml"]。ファイル全体を1つのFragment（location="text"）として扱う。エンコーディングは utf-8-sig → utf-8 → cp932 → charset-normalizer の順で判定し、**出力は元のエンコーディング・改行をバイト忠実に維持**。判定不能時は `UnicodeError`
  - `TextHandler.detect_encoding(path) -> str`（pipelineのエラー分類用に公開）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/handlers/test_text_handler.py
import pytest
from privacyprotection.handlers.text_handler import TextHandler

h = TextHandler()

def _roundtrip(tmp_path, name, data: bytes):
    src = tmp_path / name
    src.write_bytes(data)
    frags = h.read_fragments(src)
    dst = tmp_path / f"out_{name}"
    h.write_fragments(src, dst, frags)  # 無加工で書き戻し
    return src.read_bytes(), dst.read_bytes()

def test_utf8_roundtrip_byte_identical(tmp_path):
    a, b = _roundtrip(tmp_path, "a.txt", "山田太郎\nです\n".encode("utf-8"))
    assert a == b

def test_utf8_bom_preserved(tmp_path):
    a, b = _roundtrip(tmp_path, "b.txt", "﻿山田\r\n".encode("utf-8"))
    assert a == b

def test_cp932_roundtrip_byte_identical(tmp_path):
    a, b = _roundtrip(tmp_path, "c.txt", "山田太郎です\r\n".encode("cp932"))
    assert a == b

def test_masked_text_written_in_original_encoding(tmp_path):
    src = tmp_path / "d.txt"
    src.write_bytes("山田太郎".encode("cp932"))
    frags = h.read_fragments(src)
    frags[0].text = "【人名_1】"
    dst = tmp_path / "d_masked.txt"
    h.write_fragments(src, dst, frags)
    assert dst.read_bytes() == "【人名_1】".encode("cp932")

def test_undecodable_raises(tmp_path):
    src = tmp_path / "bin.txt"
    src.write_bytes(b"\xff\xfe\x00\x01\x02\xff\xff\xff")
    with pytest.raises(UnicodeError):
        h.read_fragments(src)
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_text_handler.py -v`
Expected: FAIL

- [ ] **Step 3: base.py と text_handler.py を実装**

```python
# src/privacyprotection/handlers/base.py
"""Handler共通インターフェース（設計書 4.5）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..core.models import Fragment


class FileHandler(ABC):
    extensions: ClassVar[list[str]] = []

    @abstractmethod
    def read_fragments(self, path: Path) -> list[Fragment]: ...

    @abstractmethod
    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None: ...
```

```python
# src/privacyprotection/handlers/text_handler.py
"""テキスト系ファイル。元エンコーディング・改行をバイト忠実に維持する（設計書 4.7）。"""
from __future__ import annotations

from pathlib import Path

from ..core.models import Fragment
from .base import FileHandler


class TextHandler(FileHandler):
    extensions = [".txt", ".csv", ".json", ".md", ".log", ".xml", ".html", ".yaml", ".yml"]

    def detect_encoding(self, path: Path) -> str:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                raw.decode(enc)
                # BOMなしファイルを utf-8-sig で読むと BOM 誤除去はないが、
                # BOM付きは utf-8-sig が先にマッチするためこの順で安全
                if enc == "utf-8-sig" and not raw.startswith(b"\xef\xbb\xbf"):
                    continue
                return enc
            except UnicodeDecodeError:
                continue
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is None:
            raise UnicodeError(f"エンコーディングを判定できません: {path.name}")
        return best.encoding

    def read_fragments(self, path: Path) -> list[Fragment]:
        enc = self.detect_encoding(path)
        # newline を変換しないよう bytes からデコード（改行コード維持）
        text = path.read_bytes().decode(enc)
        frag = Fragment(text=text, location="text")
        frag.encoding = enc  # write時に使う付加情報
        return [frag]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        enc = getattr(masked[0], "encoding", None) or self.detect_encoding(src)
        dst.write_bytes(masked[0].text.encode(enc))
```

注意: `Fragment` は dataclass のため任意属性を付加できない場合がある。その場合は `models.py` の `Fragment` に `encoding: str | None = None` フィールドを追加する（テストを壊さない追加変更として許可）。

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_text_handler.py tests/core -v`
Expected: PASS（コア層の既存テスト含め全件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/handlers tests/handlers
git commit -m "feat: add file handler base and encoding-preserving text handler"
```

---

### Task 10: XlsxHandler

**Files:**
- Create: `src/privacyprotection/handlers/xlsx_handler.py`
- Test: `tests/handlers/test_xlsx_handler.py`

**Interfaces:**
- Consumes: `FileHandler`, `Fragment`
- Produces: `XlsxHandler`（extensions=[".xlsx"]）。マスク対象（設計書4.6）: 文字列セル値・セルコメント・ヘッダーフッター・シート名。数式・数値セルは対象外（Fragmentにしない）。location は `"Sheet1!A1"` / `"Sheet1!A1#comment"` / `"Sheet1#header"` / `"sheetname:Sheet1"` 形式

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/handlers/test_xlsx_handler.py
import openpyxl
from openpyxl.comments import Comment
from privacyprotection.handlers.xlsx_handler import XlsxHandler

h = XlsxHandler()

def make_book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "名簿"
    ws["A1"] = "山田太郎"
    ws["B1"] = 42          # 数値 → 対象外
    ws["A2"] = "=SUM(1,2)" # 数式 → 対象外
    ws["A3"] = "佐藤花子"
    ws["A3"].comment = Comment("連絡先: 090-1111-2222", "author")
    ws.oddHeader.center.text = "社外秘 山田太郎"
    p = tmp_path / "test.xlsx"
    wb.save(p)
    return p

def test_reads_string_cells_comments_header_sheetname(tmp_path):
    frags = h.read_fragments(make_book(tmp_path))
    texts = {f.location: f.text for f in frags}
    assert texts["名簿!A1"] == "山田太郎"
    assert texts["名簿!A3"] == "佐藤花子"
    assert texts["名簿!A3#comment"] == "連絡先: 090-1111-2222"
    assert texts["名簿#header"] == "社外秘 山田太郎"
    assert texts["sheetname:名簿"] == "名簿"
    assert "名簿!B1" not in texts   # 数値は対象外
    assert "名簿!A2" not in texts   # 数式は対象外

def test_write_replaces_only_masked_text(tmp_path):
    src = make_book(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.xlsx"
    h.write_fragments(src, dst, frags)
    wb = openpyxl.load_workbook(dst)
    ws = wb["名簿"]
    assert ws["A1"].value == "【人名_1】"
    assert ws["B1"].value == 42
    assert ws["A3"].value == "佐藤花子"
    assert ws.oddHeader.center.text == "社外秘 【人名_1】"
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_xlsx_handler.py -v`
Expected: FAIL

- [ ] **Step 3: xlsx_handler.py を実装**

```python
# src/privacyprotection/handlers/xlsx_handler.py
"""xlsx。対象: 文字列セル・コメント・ヘッダーフッター・シート名（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

import openpyxl

from ..core.models import Fragment
from .base import FileHandler

_HF_PARTS = ("left", "center", "right")


class XlsxHandler(FileHandler):
    extensions = [".xlsx"]

    def read_fragments(self, path: Path) -> list[Fragment]:
        wb = openpyxl.load_workbook(path)
        frags: list[Fragment] = []
        for ws in wb.worksheets:
            frags.append(Fragment(text=ws.title, location=f"sheetname:{ws.title}"))
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and not cell.value.startswith("="):
                        frags.append(Fragment(
                            text=cell.value,
                            location=f"{ws.title}!{cell.coordinate}"))
                    if cell.comment is not None:
                        frags.append(Fragment(
                            text=cell.comment.text,
                            location=f"{ws.title}!{cell.coordinate}#comment"))
            hf_text = ws.oddHeader.center.text
            if hf_text:
                frags.append(Fragment(text=hf_text, location=f"{ws.title}#header"))
            ft_text = ws.oddFooter.center.text
            if ft_text:
                frags.append(Fragment(text=ft_text, location=f"{ws.title}#footer"))
        return frags

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        wb = openpyxl.load_workbook(src)
        by_loc = {f.location: f.text for f in masked}
        for ws in wb.worksheets:
            orig_title = ws.title
            for row in ws.iter_rows():
                for cell in row:
                    loc = f"{orig_title}!{cell.coordinate}"
                    if loc in by_loc:
                        cell.value = by_loc[loc]
                    cloc = f"{loc}#comment"
                    if cloc in by_loc and cell.comment is not None:
                        cell.comment.text = by_loc[cloc]
            if f"{orig_title}#header" in by_loc:
                ws.oddHeader.center.text = by_loc[f"{orig_title}#header"]
            if f"{orig_title}#footer" in by_loc:
                ws.oddFooter.center.text = by_loc[f"{orig_title}#footer"]
            new_title = by_loc.get(f"sheetname:{orig_title}")
            if new_title and new_title != orig_title:
                ws.title = new_title
        wb.save(dst)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_xlsx_handler.py -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/handlers/xlsx_handler.py tests/handlers/test_xlsx_handler.py
git commit -m "feat: add xlsx handler covering cells, comments, header/footer, sheet names"
```

---

### Task 11: DocxHandler

**Files:**
- Create: `src/privacyprotection/handlers/docx_handler.py`
- Test: `tests/handlers/test_docx_handler.py`

**Interfaces:**
- Consumes: `FileHandler`, `Fragment`
- Produces: `DocxHandler`（extensions=[".docx"]）。対象: 本文段落・表セル・ヘッダーフッター。段落は**段落単位**のFragment（location=`"para:<n>"`、`"table:<t>:<r>:<c>"`、`"header:<s>:<n>"`、`"footer:<s>:<n>"`）。書き戻しは段落内の最初のrunに全テキストを設定し残りのrunを空にする（run単位の書式は段落先頭のものに揃う。設計書4.6「レイアウト崩れ許容」の範囲内）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/handlers/test_docx_handler.py
import docx
from privacyprotection.handlers.docx_handler import DocxHandler

h = DocxHandler()

def make_doc(tmp_path):
    d = docx.Document()
    d.add_paragraph("担当: 山田太郎")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "佐藤花子"
    t.cell(0, 1).text = "090-1111-2222"
    d.sections[0].header.paragraphs[0].text = "社外秘 山田太郎"
    p = tmp_path / "test.docx"
    d.save(p)
    return p

def test_reads_paragraphs_tables_header(tmp_path):
    frags = h.read_fragments(make_doc(tmp_path))
    texts = {f.location: f.text for f in frags}
    assert texts["para:0"] == "担当: 山田太郎"
    assert texts["table:0:0:0"] == "佐藤花子"
    assert texts["table:0:0:1"] == "090-1111-2222"
    assert any(loc.startswith("header:") and "山田太郎" in t
               for loc, t in texts.items())

def test_write_replaces_text(tmp_path):
    src = make_doc(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.docx"
    h.write_fragments(src, dst, frags)
    d = docx.Document(str(dst))
    assert d.paragraphs[0].text == "担当: 【人名_1】"
    assert d.tables[0].cell(0, 0).text == "佐藤花子"
    assert "【人名_1】" in d.sections[0].header.paragraphs[0].text
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_docx_handler.py -v`
Expected: FAIL

- [ ] **Step 3: docx_handler.py を実装**

```python
# src/privacyprotection/handlers/docx_handler.py
"""docx。対象: 本文段落・表・ヘッダーフッター（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

import docx

from ..core.models import Fragment
from .base import FileHandler


def _set_paragraph_text(paragraph, text: str) -> None:
    """段落テキストを差し替える。書式は先頭runのものに揃う。"""
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


class DocxHandler(FileHandler):
    extensions = [".docx"]

    def _iter_locations(self, doc):
        for i, p in enumerate(doc.paragraphs):
            yield f"para:{i}", p
        for ti, table in enumerate(doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        loc = f"table:{ti}:{ri}:{ci}" if pi == 0 else f"table:{ti}:{ri}:{ci}:p{pi}"
                        yield loc, p
        for si, section in enumerate(doc.sections):
            for pi, p in enumerate(section.header.paragraphs):
                yield f"header:{si}:{pi}", p
            for pi, p in enumerate(section.footer.paragraphs):
                yield f"footer:{si}:{pi}", p

    def read_fragments(self, path: Path) -> list[Fragment]:
        doc = docx.Document(str(path))
        return [Fragment(text=p.text, location=loc)
                for loc, p in self._iter_locations(doc) if p.text]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        doc = docx.Document(str(src))
        by_loc = {f.location: f.text for f in masked}
        for loc, p in self._iter_locations(doc):
            if loc in by_loc and p.text != by_loc[loc]:
                _set_paragraph_text(p, by_loc[loc])
        doc.save(str(dst))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_docx_handler.py -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/handlers/docx_handler.py tests/handlers/test_docx_handler.py
git commit -m "feat: add docx handler covering paragraphs, tables, header/footer"
```

---

### Task 12: PptxHandler

**Files:**
- Create: `src/privacyprotection/handlers/pptx_handler.py`
- Test: `tests/handlers/test_pptx_handler.py`

**Interfaces:**
- Consumes: `FileHandler`, `Fragment`
- Produces: `PptxHandler`（extensions=[".pptx"]）。対象: スライド内シェイプのテキスト（段落単位）・表・スピーカーノート。location=`"slide:<s>:shape:<i>:para:<p>"`、`"slide:<s>:table:<i>:<r>:<c>"`、`"slide:<s>:notes:para:<p>"`

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/handlers/test_pptx_handler.py
from pptx import Presentation
from pptx.util import Inches
from privacyprotection.handlers.pptx_handler import PptxHandler

h = PptxHandler()

def make_pptx(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text = "山田太郎の報告"
    box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "連絡先: 090-1111-2222"
    slide.notes_slide.notes_text_frame.text = "佐藤花子に確認"
    p = tmp_path / "test.pptx"
    prs.save(p)
    return p

def test_reads_shapes_and_notes(tmp_path):
    frags = h.read_fragments(make_pptx(tmp_path))
    all_text = " ".join(f.text for f in frags)
    assert "山田太郎の報告" in all_text
    assert "連絡先: 090-1111-2222" in all_text
    assert "佐藤花子に確認" in all_text

def test_write_replaces_text(tmp_path):
    src = make_pptx(tmp_path)
    frags = h.read_fragments(src)
    for f in frags:
        f.text = f.text.replace("山田太郎", "【人名_1】")
    dst = tmp_path / "out.pptx"
    h.write_fragments(src, dst, frags)
    out_frags = h.read_fragments(dst)
    all_text = " ".join(f.text for f in out_frags)
    assert "【人名_1】の報告" in all_text
    assert "山田太郎" not in all_text
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_pptx_handler.py -v`
Expected: FAIL

- [ ] **Step 3: pptx_handler.py を実装**

```python
# src/privacyprotection/handlers/pptx_handler.py
"""pptx。対象: シェイプテキスト・表・スピーカーノート（設計書 4.6）。"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from ..core.models import Fragment
from .base import FileHandler


def _set_paragraph_text(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.text = text


class PptxHandler(FileHandler):
    extensions = [".pptx"]

    def _iter_locations(self, prs):
        for si, slide in enumerate(prs.slides):
            for hi, shape in enumerate(slide.shapes):
                if shape.has_text_frame:
                    for pi, para in enumerate(shape.text_frame.paragraphs):
                        yield f"slide:{si}:shape:{hi}:para:{pi}", para
                if shape.has_table:
                    for ri, row in enumerate(shape.table.rows):
                        for ci, cell in enumerate(row.cells):
                            for pi, para in enumerate(cell.text_frame.paragraphs):
                                yield f"slide:{si}:table:{hi}:{ri}:{ci}:para:{pi}", para
            if slide.has_notes_slide:
                for pi, para in enumerate(
                        slide.notes_slide.notes_text_frame.paragraphs):
                    yield f"slide:{si}:notes:para:{pi}", para

    @staticmethod
    def _para_text(para) -> str:
        return "".join(run.text for run in para.runs)

    def read_fragments(self, path: Path) -> list[Fragment]:
        prs = Presentation(str(path))
        return [Fragment(text=self._para_text(p), location=loc)
                for loc, p in self._iter_locations(prs) if self._para_text(p)]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        prs = Presentation(str(src))
        by_loc = {f.location: f.text for f in masked}
        for loc, para in self._iter_locations(prs):
            if loc in by_loc and self._para_text(para) != by_loc[loc]:
                _set_paragraph_text(para, by_loc[loc])
        prs.save(str(dst))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_pptx_handler.py -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/handlers/pptx_handler.py tests/handlers/test_pptx_handler.py
git commit -m "feat: add pptx handler covering shapes, tables, speaker notes"
```

---

### Task 13: Handlerレジストリと FolderWalker

**Files:**
- Create: `src/privacyprotection/handlers/registry.py`, `src/privacyprotection/handlers/folder_walker.py`
- Test: `tests/handlers/test_folder_walker.py`

**Interfaces:**
- Consumes: 各Handler（Task 9-12）
- Produces:
  - `registry.get_handler(path: Path) -> FileHandler | None`（未対応拡張子は None）
  - `folder_walker.walk(root: Path) -> WalkResult`。`WalkResult(supported: list[Path], skipped: list[tuple[Path, str]])`（skipped は (パス, 理由)）。設計書5.4: シンボリックリンク非追従、隠し/システム/`~$` ファイル除外、`_masked` 付き・`.pmap.csv`・`_report.txt` は再処理しない

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/handlers/test_folder_walker.py
from privacyprotection.handlers.folder_walker import walk
from privacyprotection.handlers.registry import get_handler
from privacyprotection.handlers.text_handler import TextHandler
from privacyprotection.handlers.xlsx_handler import XlsxHandler


def test_registry_resolves_by_extension(tmp_path):
    assert isinstance(get_handler(tmp_path / "a.txt"), TextHandler)
    assert isinstance(get_handler(tmp_path / "A.XLSX"), XlsxHandler)  # 大文字も可
    assert get_handler(tmp_path / "a.pdf") is None


def test_walk_collects_supported_and_skips(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub" / "b.md").write_text("x")
    (tmp_path / "c.pdf").write_bytes(b"x")            # 未対応
    (tmp_path / "~$lock.docx").write_bytes(b"x")      # Officeロックファイル
    (tmp_path / "d_masked.txt").write_text("x")       # 既存出力
    (tmp_path / "e.pmap.csv").write_text("x")         # 対応表
    (tmp_path / "_report.txt").write_text("x")        # レポート
    r = walk(tmp_path)
    names = sorted(p.name for p in r.supported)
    assert names == ["a.txt", "b.md"]
    skipped_names = {p.name for p, _ in r.skipped}
    assert "c.pdf" in skipped_names
    assert "~$lock.docx" in skipped_names
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_folder_walker.py -v`
Expected: FAIL

- [ ] **Step 3: registry.py と folder_walker.py を実装**

```python
# src/privacyprotection/handlers/registry.py
"""拡張子→Handler解決。"""
from __future__ import annotations

from pathlib import Path

from .base import FileHandler
from .docx_handler import DocxHandler
from .pptx_handler import PptxHandler
from .text_handler import TextHandler
from .xlsx_handler import XlsxHandler

_HANDLERS: list[FileHandler] = [TextHandler(), XlsxHandler(), DocxHandler(), PptxHandler()]
_BY_EXT = {ext: h for h in _HANDLERS for ext in h.extensions}


def get_handler(path: Path) -> FileHandler | None:
    return _BY_EXT.get(path.suffix.lower())


def supported_extensions() -> list[str]:
    return sorted(_BY_EXT)
```

```python
# src/privacyprotection/handlers/folder_walker.py
"""フォルダ再帰列挙（設計書 5.4）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .registry import get_handler


@dataclass
class WalkResult:
    supported: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _is_own_output(p: Path) -> bool:
    name = p.name
    return ("_masked" in p.stem) or name.endswith(".pmap.csv") or name == "_report.txt"


def walk(root: Path) -> WalkResult:
    result = WalkResult()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 隠しディレクトリは辿らない
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                result.skipped.append((p, "シンボリックリンク"))
                continue
            if name.startswith(("~$", ".")):
                result.skipped.append((p, "一時/隠しファイル"))
                continue
            if _is_own_output(p):
                continue  # 本アプリの出力物は黙って除外（レポート対象にもしない）
            if get_handler(p) is None:
                result.skipped.append((p, "未対応の拡張子"))
                continue
            result.supported.append(p)
    return result
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/handlers/test_folder_walker.py -v`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/handlers/registry.py src/privacyprotection/handlers/folder_walker.py tests/handlers/test_folder_walker.py
git commit -m "feat: add handler registry and folder walker with edge-case handling"
```

---

### Task 14: レポートモデル

**Files:**
- Create: `src/privacyprotection/services/__init__.py`, `src/privacyprotection/services/report.py`
- Test: `tests/services/test_report.py`

**Interfaces:**
- Consumes: `CATEGORY_LABELS`（Task 1）
- Produces:
  - `FileReport(path: Path, category_counts: dict[str, int], notes: list[str], error: str | None = None)`。`total()` で合計件数。
  - `BatchReport(files: list[FileReport], skipped: list[tuple[Path, str]])`。`zero_detection_files() -> list[FileReport]`（error なしで total()==0）、`render_text() -> str`（`_report.txt` の内容。検出0件ファイルに「⚠ 検出0件」を付す。**元の値は含めない**）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/services/test_report.py
from pathlib import Path
from privacyprotection.services.report import BatchReport, FileReport


def test_file_report_total():
    r = FileReport(Path("a.txt"), {"PERSON": 3, "PHONE": 1}, [])
    assert r.total() == 4

def test_zero_detection_files_highlighted():
    ok = FileReport(Path("a.txt"), {"PERSON": 1}, [])
    zero = FileReport(Path("b.txt"), {}, [])
    err = FileReport(Path("c.txt"), {}, [], error="読み込み失敗")
    br = BatchReport(files=[ok, zero, err], skipped=[])
    assert [f.path.name for f in br.zero_detection_files()] == ["b.txt"]

def test_render_text_contains_counts_warnings_and_no_values():
    br = BatchReport(
        files=[
            FileReport(Path("a.txt"), {"PERSON": 2}, ["図形内テキストは対象外です"]),
            FileReport(Path("b.txt"), {}, []),
        ],
        skipped=[(Path("c.pdf"), "未対応の拡張子")],
    )
    text = br.render_text()
    assert "a.txt" in text and "人名: 2" in text
    assert "⚠ 検出0件" in text and "b.txt" in text
    assert "c.pdf" in text and "未対応の拡張子" in text
    assert "図形内テキストは対象外です" in text
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/services/test_report.py -v`
Expected: FAIL

- [ ] **Step 3: report.py を実装**

```python
# src/privacyprotection/services/report.py
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
            counts = "、".join(
                f"{CATEGORY_LABELS[c]}: {n}" for c, n in sorted(f.category_counts.items())
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/services/test_report.py -v`
Expected: PASS（3件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/services tests/services
git commit -m "feat: add post-processing report models with zero-detection warning"
```

---

### Task 15: Pipeline（マスク・復元のオーケストレーション、原子的出力）

**Files:**
- Create: `src/privacyprotection/services/pipeline.py`
- Test: `tests/services/test_pipeline.py`

**Interfaces:**
- Consumes: すべてのコア層・Handler層・レポート（Task 1-14）
- Produces:
  - `Pipeline(detector: Detector, mode: str)`
  - `.analyze_file(path) -> tuple[list[Fragment], list[list[Detection]]]`（プレビュー用: 断片と検出リストを返す）
  - `.mask_file(path, fragments, detections, out_dir=None) -> tuple[Path, FileReport]`（analyze の結果[プレビューで編集済みでもよい]を受けてマスク実行。出力名 `<stem>_masked<suffix>`、既存なら `<stem>_masked(2)<suffix>`。可逆時 `.pmap.csv` を**先に**確定。一時ファイル→`os.replace`）
  - `.mask_folder(root, out_dir=None, progress=None) -> tuple[BatchReport, Path | None]`（共有Maskerで横断一貫トークン。`_folder.pmap.csv` と `_report.txt` をルートに出力。progress は `Callable[[int, int, Path], None]`）
  - `.restore_text(text, mapping_path) -> RestoreResult`
  - `.restore_file(masked_path, mapping_path=None, out_dir=None) -> tuple[Path, RestoreResult]`（mapping_path 省略時は `<masked名>.pmap.csv` を同フォルダから自動検出。出力名 `<stem>_restored<suffix>`）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/services/test_pipeline.py
import pytest
from privacyprotection.core.detector import Detector
from privacyprotection.core.dictionary import DictionaryDetector
from privacyprotection.core.mapping_io import read_mapping
from privacyprotection.core.patterns import PatternDetector
from privacyprotection.services.pipeline import Pipeline


def make_pipeline(mode="token"):
    det = Detector([PatternDetector(),
                    DictionaryDetector({"山田太郎": "PERSON", "佐藤花子": "PERSON"})])
    return Pipeline(detector=det, mode=mode)


def test_mask_file_outputs_masked_and_mapping(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎 03-1234-5678", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    out, report = pl.mask_file(src, frags, dets)
    assert out.name == "memo_masked.txt"
    assert out.read_text(encoding="utf-8") == "【人名_1】 【電話_1】"
    assert src.read_text(encoding="utf-8") == "山田太郎 03-1234-5678"  # 元ファイル不変
    mapping = read_mapping(tmp_path / "memo_masked.txt.pmap.csv")
    assert len(mapping.entries) == 2
    assert report.category_counts == {"PERSON": 1, "PHONE": 1}


def test_existing_output_gets_numbered_suffix(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    (tmp_path / "memo_masked.txt").write_text("既存", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    out, _ = pl.mask_file(src, frags, dets)
    assert out.name == "memo_masked(2).txt"
    assert (tmp_path / "memo_masked.txt").read_text(encoding="utf-8") == "既存"


def test_redact_mode_produces_no_mapping(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline(mode="redact")
    frags, dets = pl.analyze_file(src)
    out, _ = pl.mask_file(src, frags, dets)
    assert out.read_text(encoding="utf-8") == "●●●●"
    assert not (tmp_path / "memo_masked.txt.pmap.csv").exists()


def test_disabled_detection_respected(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎と佐藤花子", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    for d in dets[0]:
        if d.text == "佐藤花子":
            d.enabled = False
    out, report = pl.mask_file(src, frags, dets)
    assert out.read_text(encoding="utf-8") == "【人名_1】と佐藤花子"


def test_mask_folder_shared_tokens_and_report(tmp_path):
    (tmp_path / "a.txt").write_text("山田太郎", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("山田太郎の件", encoding="utf-8")
    (tmp_path / "c.pdf").write_bytes(b"x")
    pl = make_pipeline()
    batch, mapping_path = pl.mask_folder(tmp_path)
    # 同一人物は全ファイルで同一トークン
    assert (tmp_path / "a_masked.txt").read_text(encoding="utf-8") == "【人名_1】"
    assert (tmp_path / "sub" / "b_masked.txt").read_text(encoding="utf-8") == "【人名_1】の件"
    assert mapping_path == tmp_path / "_folder.pmap.csv"
    assert (tmp_path / "_report.txt").exists()
    assert any(p.name == "c.pdf" for p, _ in batch.skipped)


def test_restore_file_roundtrip(tmp_path):
    src = tmp_path / "memo.txt"
    original = "山田太郎 03-1234-5678 佐藤花子"
    src.write_text(original, encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    restored_path, result = pl.restore_file(masked_path)  # pmap自動検出
    assert restored_path.read_text(encoding="utf-8") == original
    assert result.unknown_tokens == []


def test_restore_text_with_unknown_token(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline()
    frags, dets = pl.analyze_file(src)
    masked_path, _ = pl.mask_file(src, frags, dets)
    r = pl.restore_text("【人名_1】と【人名_9】",
                        masked_path.parent / (masked_path.name + ".pmap.csv"))
    assert r.text == "山田太郎と【人名_9】"
    assert r.unknown_tokens == ["【人名_9】"]


def test_broken_file_reported_not_raised(tmp_path):
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"not a zip")
    pl = make_pipeline()
    batch, _ = pl.mask_folder(tmp_path)
    assert len(batch.files) == 1
    assert batch.files[0].error is not None
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/services/test_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: pipeline.py を実装**

```python
# src/privacyprotection/services/pipeline.py
"""マスク・復元のオーケストレーション（設計書 5章）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from ..core.detector import Detector
from ..core.mapping_io import read_mapping, write_mapping
from ..core.masker import Masker
from ..core.models import Detection, Fragment, MappingTable, RestoreResult
from ..core.restorer import Restorer
from ..handlers.folder_walker import walk
from ..handlers.registry import get_handler
from ..handlers.text_handler import TextHandler
from .report import BatchReport, FileReport

MASKED_SUFFIX = "_masked"


def _numbered_output(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{MASKED_SUFFIX}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}{MASKED_SUFFIX}({n}){suffix}"
        n += 1
    return candidate


def _atomic_write(write_fn: Callable[[Path], None], final_path: Path) -> None:
    """一時ファイルに書いてからリネームで確定（設計書 5.3）。"""
    fd, tmp_name = tempfile.mkstemp(dir=final_path.parent,
                                    suffix=final_path.suffix + ".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write_fn(tmp)
        os.replace(tmp, final_path)
    finally:
        tmp.unlink(missing_ok=True)


class Pipeline:
    def __init__(self, detector: Detector, mode: str):
        self._detector = detector
        self._mode = mode

    def analyze_file(self, path: Path):
        handler = get_handler(path)
        if handler is None:
            raise ValueError(f"未対応の拡張子です: {path.suffix}")
        fragments = handler.read_fragments(path)
        detections = [self._detector.detect(f.text) for f in fragments]
        return fragments, detections

    def mask_file(self, path: Path, fragments: list[Fragment],
                  detections: list[list[Detection]],
                  out_dir: Path | None = None,
                  masker: Masker | None = None) -> tuple[Path, FileReport]:
        handler = get_handler(path)
        m = masker or Masker(mode=self._mode)
        m.scan_existing_tokens([f.text for f in fragments])
        masked_texts, table = m.mask_fragments(
            [f.text for f in fragments], detections)
        masked_frags = []
        for frag, text in zip(fragments, masked_texts):
            frag_copy = Fragment(text=text, location=frag.location)
            frag_copy.encoding = getattr(frag, "encoding", None)
            masked_frags.append(frag_copy)

        directory = out_dir or path.parent
        out_path = _numbered_output(directory, path.stem, path.suffix)

        # 対応表を先に確定（設計書 5.3）。共有Masker（フォルダ一括）時は呼び出し元が書く
        if self._mode == "token" and masker is None:
            mapping_path = directory / (out_path.name + ".pmap.csv")
            _atomic_write(lambda p: write_mapping(table, p), mapping_path)

        _atomic_write(lambda p: handler.write_fragments(path, p, masked_frags),
                      out_path)

        counts: dict[str, int] = {}
        for dets in detections:
            for d in dets:
                if d.enabled:
                    counts[d.category] = counts.get(d.category, 0) + 1
        notes = []
        if path.suffix.lower() in (".xlsx", ".docx", ".pptx"):
            notes.append("図形/テキストボックス内の文字と埋込オブジェクトは対象外です")
        return out_path, FileReport(path=path, category_counts=counts, notes=notes)

    def mask_folder(self, root: Path, out_dir: Path | None = None,
                    progress: Callable[[int, int, Path], None] | None = None
                    ) -> tuple[BatchReport, Path | None]:
        walk_result = walk(root)
        shared_masker = Masker(mode=self._mode)
        batch = BatchReport(skipped=list(walk_result.skipped))
        total = len(walk_result.supported)
        for i, path in enumerate(walk_result.supported, start=1):
            if progress:
                progress(i, total, path)
            try:
                fragments, detections = self.analyze_file(path)
                _, file_report = self.mask_file(
                    path, fragments, detections, out_dir=out_dir,
                    masker=shared_masker)
                batch.files.append(file_report)
            except Exception as exc:  # 個別失敗で一括処理は止めない（設計書 7章）
                batch.files.append(FileReport(
                    path=path, category_counts={}, notes=[],
                    error=f"{type(exc).__name__}: 処理できませんでした"))
        mapping_path: Path | None = None
        if self._mode == "token":
            _, table = shared_masker.mask_fragments([], [])  # 蓄積済みテーブル取得
            mapping_path = root / "_folder.pmap.csv"
            _atomic_write(lambda p: write_mapping(table, p), mapping_path)
        report_path = root / "_report.txt"
        _atomic_write(
            lambda p: p.write_text(batch.render_text(), encoding="utf-8"),
            report_path)
        return batch, mapping_path

    def restore_text(self, text: str, mapping_path: Path) -> RestoreResult:
        return Restorer(read_mapping(mapping_path)).restore(text)

    def restore_file(self, masked_path: Path, mapping_path: Path | None = None,
                     out_dir: Path | None = None) -> tuple[Path, RestoreResult]:
        if mapping_path is None:
            candidate = masked_path.parent / (masked_path.name + ".pmap.csv")
            if not candidate.exists():
                raise FileNotFoundError("対応表(.pmap.csv)が見つかりません")
            mapping_path = candidate
        restorer = Restorer(read_mapping(mapping_path))
        handler = get_handler(masked_path)
        if handler is None:
            raise ValueError(f"未対応の拡張子です: {masked_path.suffix}")
        fragments = handler.read_fragments(masked_path)
        all_unknown: list[str] = []
        for frag in fragments:
            result = restorer.restore(frag.text)
            frag.text = result.text
            all_unknown.extend(t for t in result.unknown_tokens
                               if t not in all_unknown)
        directory = out_dir or masked_path.parent
        stem = masked_path.stem.replace(MASKED_SUFFIX, "") or masked_path.stem
        out_path = directory / f"{stem}_restored{masked_path.suffix}"
        n = 2
        while out_path.exists():
            out_path = directory / f"{stem}_restored({n}){masked_path.suffix}"
            n += 1
        _atomic_write(
            lambda p: handler.write_fragments(masked_path, p, fragments), out_path)
        return out_path, RestoreResult(text="", unknown_tokens=all_unknown)
```

実装メモ:
- `Fragment` に `encoding` フィールドが必要（Task 9 の注意参照）。未追加ならここで `models.py` に `encoding: str | None = None` を追加する
- `mask_fragments([], [])` で蓄積済みテーブルを取り出せるのは Masker が `_value_tokens` を保持するため

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests -v --ignore=tests/core/test_ner.py`
Expected: PASS（全件）。`test_ner.py` はモデル導入済みなら含めて実行

- [ ] **Step 5: コミット**

```bash
git add -A
git commit -m "feat: add mask/restore pipeline with atomic output and batch report"
```

---

### Task 16: 設定・カスタム辞書の永続化

**Files:**
- Create: `src/privacyprotection/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: なし（独立モジュール）
- Produces:
  - `AppConfig(enabled_categories: set[str], custom_dictionary: dict[str, str], output_dir: str | None, skip_preview: bool, mask_mode: str)`（既定: 全カテゴリ有効、辞書空、output_dir=None[元と同じ]、skip_preview=False、mask_mode="token"）
  - `load_config(path: Path | None = None) -> AppConfig`（既定パス `%APPDATA%/PrivacyProtection/config.json`。無ければ既定値）
  - `save_config(cfg: AppConfig, path: Path | None = None) -> None`
  - `import_dictionary_csv(path: Path) -> tuple[dict[str, str], list[str]]`（列 `語句,種別`、戻りは (辞書, 警告リスト[重複語句等])）
  - `export_dictionary_csv(d: dict[str, str], path: Path) -> None`（UTF-8 BOM）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/test_config.py
from privacyprotection.config import (
    AppConfig, export_dictionary_csv, import_dictionary_csv,
    load_config, save_config,
)

def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "none.json")
    assert cfg.mask_mode == "token"
    assert cfg.skip_preview is False
    assert cfg.custom_dictionary == {}
    assert "PERSON" in cfg.enabled_categories

def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = AppConfig(enabled_categories={"PERSON"},
                    custom_dictionary={"PRJ-001": "CUSTOM"},
                    output_dir="C:/out", skip_preview=True, mask_mode="redact")
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded == cfg

def test_dictionary_csv_roundtrip_and_duplicate_warning(tmp_path):
    p = tmp_path / "dict.csv"
    p.write_text("語句,種別\r\n株式会社サンプル,カスタム\r\n株式会社サンプル,組織\r\n",
                 encoding="utf-8-sig")
    d, warnings = import_dictionary_csv(p)
    assert d == {"株式会社サンプル": "CUSTOM"}  # 先勝ち
    assert len(warnings) == 1
    out = tmp_path / "out.csv"
    export_dictionary_csv(d, out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
```

- [ ] **Step 2: 失敗を確認**

Run: `.venv\Scripts\python -m pytest tests/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: config.py を実装**

```python
# src/privacyprotection/config.py
"""設定・カスタム辞書の永続化（設計書 6.3）。保存先: %APPDATA%/PrivacyProtection/"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .core.models import CATEGORY_LABELS

_LABEL_TO_CATEGORY = {}
for cat, label in CATEGORY_LABELS.items():
    _LABEL_TO_CATEGORY.setdefault(label, cat)

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
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
            category = _LABEL_TO_CATEGORY.get(row[1] if len(row) > 1 else "", "CUSTOM")
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
            writer.writerow([word, CATEGORY_LABELS[category]])
```

- [ ] **Step 4: テストが通ることを確認**

Run: `.venv\Scripts\python -m pytest tests/test_config.py -v`
Expected: PASS（3件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/config.py tests/test_config.py
git commit -m "feat: add config and custom dictionary persistence"
```

---

### Task 17: GUI - ワーカーとメイン画面

**Files:**
- Create: `src/privacyprotection/gui/__init__.py`, `src/privacyprotection/gui/worker.py`, `src/privacyprotection/gui/main_window.py`, `src/privacyprotection/gui/app.py`

**Interfaces:**
- Consumes: `Pipeline`, `Detector`, 各検出器, `AppConfig`（Task 1-16）
- Produces:
  - `app.py`: `main()` エントリポイント（`python -m privacyprotection.gui.app` で起動）
  - `MaskWorker(QThread)`: シグナル `progress(int, int, str)` / `finished_ok(object)`（BatchReport または (Path, FileReport)） / `failed(str)`
  - `MainWindow(QMainWindow)`: D&Dゾーン、マスク/復元モード切替、方式選択（可逆/不可逆）、「確認なしで即変換」チェック、「貼り付け→マスク→コピー」ボタン、常時注記ラベル

GUIは自動テストなし（設計書8: 手動確認中心）。各ステップ末尾の手動確認を実施すること。

- [ ] **Step 1: worker.py を実装**

```python
# src/privacyprotection/gui/worker.py
"""バックグラウンド処理ワーカー（設計書 N-4）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class MaskWorker(QThread):
    progress = Signal(int, int, str)      # 現在, 総数, ファイル名
    finished_ok = Signal(object)          # 結果オブジェクト
    failed = Signal(str)                  # エラー要約（値は含めない）

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, progress=self._emit_progress,
                              **self._kwargs) \
                if "progress_supported" in self._kwargs.pop("_flags", []) \
                else self._fn(*self._args, **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: 処理に失敗しました")

    def _emit_progress(self, i: int, total: int, path: Path):
        self.progress.emit(i, total, path.name)
```

- [ ] **Step 2: main_window.py を実装**

```python
# src/privacyprotection/gui/main_window.py
"""メイン画面（設計書 6.1）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ..config import load_config, save_config
from ..core.detector import Detector
from ..core.dictionary import DictionaryDetector
from ..core.ner import NerDetector
from ..core.patterns import PatternDetector
from ..services.pipeline import Pipeline
from .worker import MaskWorker

NOTICE = "自動検出は完全ではありません。重要なデータは必ずプレビューで確認してください"


class DropZone(QLabel):
    def __init__(self, on_paths):
        super().__init__("ここにファイル／フォルダをドロップ\n（またはクリックして選択）")
        self._on_paths = on_paths
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px;"
            " padding: 40px; font-size: 14px; }")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        self._on_paths(paths)

    def mousePressEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(self, "ファイルを選択")
        if files:
            self._on_paths([Path(f) for f in files])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("個人情報マスキング")
        self.resize(560, 480)
        self._config = load_config()
        self._worker: MaskWorker | None = None

        central = QWidget()
        layout = QVBoxLayout(central)

        # モード切替
        mode_row = QHBoxLayout()
        self.mask_radio = QRadioButton("マスク")
        self.restore_radio = QRadioButton("復元")
        self.mask_radio.setChecked(True)
        g1 = QButtonGroup(self)
        g1.addButton(self.mask_radio); g1.addButton(self.restore_radio)
        mode_row.addWidget(self.mask_radio); mode_row.addWidget(self.restore_radio)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 方式選択
        method_row = QHBoxLayout()
        self.token_radio = QRadioButton("可逆（トークン）")
        self.redact_radio = QRadioButton("不可逆（塗りつぶし）")
        (self.token_radio if self._config.mask_mode == "token"
         else self.redact_radio).setChecked(True)
        g2 = QButtonGroup(self)
        g2.addButton(self.token_radio); g2.addButton(self.redact_radio)
        method_row.addWidget(self.token_radio); method_row.addWidget(self.redact_radio)
        method_row.addStretch()
        layout.addLayout(method_row)

        self.skip_preview = QCheckBox("確認なしで即変換")
        self.skip_preview.setChecked(self._config.skip_preview)
        layout.addWidget(self.skip_preview)

        self.drop_zone = DropZone(self._handle_paths)
        layout.addWidget(self.drop_zone, stretch=1)

        self.clipboard_btn = QPushButton("クリップボードをマスクしてコピー")
        self.clipboard_btn.clicked.connect(self._mask_clipboard)
        layout.addWidget(self.clipboard_btn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        notice = QLabel(NOTICE)
        notice.setStyleSheet("color: #b06000;")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self.setCentralWidget(central)

    # --- pipeline 構築 -------------------------------------------------
    def _build_pipeline(self) -> Pipeline:
        cfg = self._config
        detectors = [PatternDetector(),
                     DictionaryDetector(cfg.custom_dictionary),
                     NerDetector()]
        mode = "token" if self.token_radio.isChecked() else "redact"
        return Pipeline(
            detector=Detector(detectors,
                              enabled_categories=cfg.enabled_categories),
            mode=mode)

    # --- 入力処理 -------------------------------------------------------
    def _handle_paths(self, paths: list[Path]):
        if self.restore_radio.isChecked():
            self._restore_paths(paths)
        else:
            self._mask_paths(paths)

    def _mask_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        # フォルダ or 即変換 → バックグラウンド一括。単一ファイル＋プレビューは Task 18 で接続
        if len(paths) == 1 and paths[0].is_dir():
            self._run_worker(pipeline.mask_folder, paths[0])
        elif self.skip_preview.isChecked():
            for p in paths:
                frags, dets = pipeline.analyze_file(p)
                out, report = pipeline.mask_file(p, frags, dets)
                self._show_report_text(report_text=self._single_report(report))
        else:
            QMessageBox.information(
                self, "プレビュー",
                "プレビュー画面は次のタスクで接続します。"
                "現時点では「確認なしで即変換」をONにしてください。")

    def _restore_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        for p in paths:
            try:
                out, result = pipeline.restore_file(p)
                msg = f"復元しました: {out.name}"
                if result.unknown_tokens:
                    msg += f"\n未知トークン {len(result.unknown_tokens)} 件がそのまま残っています"
                QMessageBox.information(self, "復元完了", msg)
            except FileNotFoundError as exc:
                QMessageBox.warning(self, "復元エラー", str(exc))

    def _mask_clipboard(self):
        cb = QGuiApplication.clipboard()
        text = cb.text()
        if not text:
            QMessageBox.information(self, "クリップボード", "テキストがありません")
            return
        pipeline = self._build_pipeline()
        detections = pipeline._detector.detect(text)
        from ..core.masker import Masker
        masker = Masker(mode="token" if self.token_radio.isChecked() else "redact")
        masker.scan_existing_tokens([text])
        masked, table = masker.mask_fragments([text], [detections])
        cb.setText(masked[0])
        if table.entries:
            from ..core.mapping_io import write_mapping
            from ..config import default_config_path
            pmap = default_config_path().parent / "clipboard.pmap.csv"
            pmap.parent.mkdir(parents=True, exist_ok=True)
            write_mapping(table, pmap)
            QMessageBox.information(
                self, "マスク完了",
                f"{len(detections)}件をマスクしてコピーしました。\n対応表: {pmap}")
        else:
            QMessageBox.information(self, "マスク完了",
                                    "検出0件です（検出漏れの可能性があります）")

    # --- ワーカー実行 ----------------------------------------------------
    def _run_worker(self, fn, *args):
        self.progress.setVisible(True)
        self._worker = MaskWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, i, total, name):
        self.progress.setMaximum(total)
        self.progress.setValue(i)

    def _on_finished(self, result):
        self.progress.setVisible(False)
        batch, mapping_path = result
        self._show_report_text(batch.render_text())

    def _on_failed(self, message):
        self.progress.setVisible(False)
        QMessageBox.critical(self, "エラー", message)

    def _show_report_text(self, report_text: str):
        QMessageBox.information(self, "処理レポート", report_text)

    @staticmethod
    def _single_report(report) -> str:
        from ..services.report import BatchReport
        return BatchReport(files=[report]).render_text()

    def closeEvent(self, e):
        self._config.skip_preview = self.skip_preview.isChecked()
        self._config.mask_mode = "token" if self.token_radio.isChecked() else "redact"
        save_config(self._config)
        super().closeEvent(e)
```

```python
# src/privacyprotection/gui/app.py
"""アプリのエントリポイント。"""
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

`src/privacyprotection/gui/__init__.py` は空ファイル。

- [ ] **Step 3: 手動確認**

Run: `.venv\Scripts\python -m privacyprotection.gui.app`
確認項目:
1. ウィンドウが起動し、注記ラベルが表示される
2. 「確認なしで即変換」ONで .txt をドロップ → `_masked.txt` と `.pmap.csv` が生成され、レポートダイアログが出る
3. フォルダをドロップ → 進捗バーが動き、`_report.txt` / `_folder.pmap.csv` が生成される
4. クリップボードに個人情報入りテキストをコピー→ボタン押下→マスク済みテキストがクリップボードに入る
5. 復元モードで `_masked.txt` をドロップ → `_restored.txt` が生成される

- [ ] **Step 4: 既存テストが壊れていないことを確認**

Run: `.venv\Scripts\python -m pytest tests -v`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/gui
git commit -m "feat: add GUI main window with drag-drop, clipboard, worker thread"
```

---

### Task 18: GUI - プレビュー画面とレポート画面

**Files:**
- Create: `src/privacyprotection/gui/preview_dialog.py`, `src/privacyprotection/gui/report_dialog.py`
- Modify: `src/privacyprotection/gui/main_window.py`（`_mask_paths` のプレビュー分岐を接続、`_show_report_text` を ReportDialog に差し替え）

**Interfaces:**
- Consumes: `Fragment`, `Detection`, `Pipeline`, `CATEGORY_LABELS`
- Produces:
  - `PreviewDialog(fragments, detections, parent=None)`: `exec()` が `QDialog.Accepted` を返したら `detections`（各 Detection の enabled がユーザー操作を反映済み、手動追加分を含む）を確定として使う
  - `ReportDialog(text, parent=None)`: レポート全文をスクロール表示

- [ ] **Step 1: preview_dialog.py を実装**

```python
# src/privacyprotection/gui/preview_dialog.py
"""プレビュー画面（設計書 6.2）。左: ハイライト付き原文 / 右: 検出一覧。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem,
    QMenu, QSplitter, QTextEdit, QVBoxLayout,
)

from ..core.models import CATEGORY_LABELS, Detection

_CATEGORY_COLORS = {
    "PERSON": "#ffd0d0", "ORG": "#d0e0ff", "LOC": "#d0ffd0",
    "PHONE": "#fff0c0", "EMAIL": "#ffe0ff", "ADDRESS": "#e0fff0",
    "POSTAL": "#f0e0d0", "MYNUMBER": "#f0e0d0", "CREDITCARD": "#f0e0d0",
    "CUSTOM": "#e0e0e0",
}


class PreviewDialog(QDialog):
    def __init__(self, fragments, detections, parent=None):
        super().__init__(parent)
        self.setWindowTitle("検出結果の確認")
        self.resize(900, 600)
        self.fragments = fragments
        self.detections = detections  # list[list[Detection]] を直接編集する

        splitter = QSplitter(Qt.Horizontal)

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_view.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.text_view)

        self.list_view = QListWidget()
        self.list_view.itemChanged.connect(self._on_item_toggled)
        splitter.addWidget(self.list_view)
        splitter.setSizes([600, 300])

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("この内容でマスク実行")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(buttons)
        self._refresh()

    # フラグメント境界をまたがない前提で、全断片を連結表示する
    def _refresh(self):
        self.text_view.clear()
        self.list_view.blockSignals(True)
        self.list_view.clear()
        offset = 0
        self._offsets = []
        full = []
        for frag in self.fragments:
            self._offsets.append(offset)
            full.append(frag.text)
            offset += len(frag.text) + 1  # 区切りの改行分
        self.text_view.setPlainText("\n".join(full))

        cursor = self.text_view.textCursor()
        for fi, dets in enumerate(self.detections):
            for d in dets:
                item = QListWidgetItem(
                    f"[{CATEGORY_LABELS[d.category]}] {d.text}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if d.enabled else Qt.Unchecked)
                item.setData(Qt.UserRole, (fi, d))
                self.list_view.addItem(item)
                if d.enabled:
                    fmt = QTextCharFormat()
                    fmt.setBackground(QColor(_CATEGORY_COLORS[d.category]))
                    cursor.setPosition(self._offsets[fi] + d.start)
                    cursor.setPosition(self._offsets[fi] + d.end,
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(fmt)
        self.list_view.blockSignals(False)

    def _on_item_toggled(self, item):
        fi, d = item.data(Qt.UserRole)
        d.enabled = item.checkState() == Qt.Checked
        self._refresh()

    def _context_menu(self, pos):
        cursor = self.text_view.textCursor()
        selected = cursor.selectedText()
        if not selected:
            return
        menu = QMenu(self)
        for category, label in sorted(set(
                (c, l) for c, l in CATEGORY_LABELS.items())):
            action = QAction(f"「{selected}」を{label}として追加", menu)
            action.triggered.connect(
                lambda _=False, c=category: self._add_manual(cursor, c))
            menu.addAction(action)
        menu.exec(self.text_view.mapToGlobal(pos))

    def _add_manual(self, cursor, category):
        sel_start, sel_end = cursor.selectionStart(), cursor.selectionEnd()
        # どの断片か特定
        for fi in reversed(range(len(self._offsets))):
            if sel_start >= self._offsets[fi]:
                frag_start = sel_start - self._offsets[fi]
                frag_end = sel_end - self._offsets[fi]
                text = self.fragments[fi].text[frag_start:frag_end]
                if text:
                    self.detections[fi].append(Detection(
                        text=text, category=category,
                        start=frag_start, end=frag_end, source="manual"))
                break
        self._refresh()
```

```python
# src/privacyprotection/gui/report_dialog.py
"""処理後レポート表示（設計書 6.4）。"""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


class ReportDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("処理レポート")
        self.resize(640, 480)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        layout.addWidget(buttons)
```

- [ ] **Step 2: main_window.py にプレビューを接続**

`_mask_paths` の `else` 分岐（プレビューあり）を差し替え:

```python
        else:
            from .preview_dialog import PreviewDialog
            from .report_dialog import ReportDialog
            for p in paths:
                frags, dets = pipeline.analyze_file(p)
                dlg = PreviewDialog(frags, dets, parent=self)
                if dlg.exec() != PreviewDialog.Accepted:
                    continue
                out, report = pipeline.mask_file(p, frags, dets)
                ReportDialog(self._single_report(report), parent=self).exec()
```

`_show_report_text` も ReportDialog を使うよう差し替え:

```python
    def _show_report_text(self, report_text: str):
        from .report_dialog import ReportDialog
        ReportDialog(report_text, parent=self).exec()
```

- [ ] **Step 3: 手動確認**

Run: `.venv\Scripts\python -m privacyprotection.gui.app`
確認項目:
1. 「確認なしで即変換」OFFで .txt をドロップ → プレビューが開き、検出箇所が色分けハイライトされる
2. 右リストのチェックを外す → ハイライトが消え、確定後そのまま残る
3. 左ペインで未検出のテキストを選択→右クリック→種別を選んで追加 → マスクされる
4. キャンセルするとファイルが出力されない

- [ ] **Step 4: 既存テストが壊れていないことを確認**

Run: `.venv\Scripts\python -m pytest tests -v`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/gui
git commit -m "feat: add preview dialog with highlight/exclude/manual-add and report dialog"
```

---

### Task 19: GUI - 設定画面

**Files:**
- Create: `src/privacyprotection/gui/settings_dialog.py`
- Modify: `src/privacyprotection/gui/main_window.py`（メニューバーに「設定」を追加）

**Interfaces:**
- Consumes: `AppConfig`, `import_dictionary_csv`, `export_dictionary_csv`, `CATEGORY_LABELS`
- Produces: `SettingsDialog(config: AppConfig, parent=None)`。OKで `config` を直接更新（呼び出し元が `save_config` する）

- [ ] **Step 1: settings_dialog.py を実装**

```python
# src/privacyprotection/gui/settings_dialog.py
"""設定画面（設計書 6.3）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..config import AppConfig, export_dictionary_csv, import_dictionary_csv
from ..core.models import CATEGORY_LABELS

_TOGGLE_CATEGORIES = ["PERSON", "ORG", "LOC", "PHONE", "EMAIL",
                      "ADDRESS", "POSTAL", "MYNUMBER", "CREDITCARD"]
_LABEL_TO_CATEGORY = {}
for cat, label in CATEGORY_LABELS.items():
    _LABEL_TO_CATEGORY.setdefault(label, cat)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(560, 560)
        self._config = config
        layout = QVBoxLayout(self)

        # 検出カテゴリ ON/OFF
        cat_box = QGroupBox("検出カテゴリ")
        cat_layout = QVBoxLayout(cat_box)
        self._cat_checks = {}
        for cat in _TOGGLE_CATEGORIES:
            cb = QCheckBox(f"{CATEGORY_LABELS[cat]}（{cat}）")
            cb.setChecked(cat in config.enabled_categories)
            self._cat_checks[cat] = cb
            cat_layout.addWidget(cb)
        layout.addWidget(cat_box)

        # カスタム辞書
        dict_box = QGroupBox("カスタム辞書（語句・種別）")
        dict_layout = QVBoxLayout(dict_box)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["語句", "種別"])
        for word, cat in config.custom_dictionary.items():
            self._append_row(word, CATEGORY_LABELS[cat])
        dict_layout.addWidget(self.table)
        btn_row = QHBoxLayout()
        for label, fn in [("行を追加", self._add_row),
                          ("CSVインポート", self._import_csv),
                          ("CSVエクスポート", self._export_csv)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btn_row.addWidget(b)
        dict_layout.addLayout(btn_row)
        layout.addWidget(dict_box, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, word="", label="カスタム"):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(word))
        self.table.setItem(r, 1, QTableWidgetItem(label))

    def _add_row(self):
        self._append_row()

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "辞書CSV", filter="CSV (*.csv)")
        if not path:
            return
        try:
            from pathlib import Path
            d, warnings = import_dictionary_csv(Path(path))
        except ValueError as exc:
            QMessageBox.warning(self, "インポートエラー", str(exc))
            return
        for word, cat in d.items():
            self._append_row(word, CATEGORY_LABELS[cat])
        if warnings:
            QMessageBox.information(self, "警告", "\n".join(warnings))

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "辞書CSV", filter="CSV (*.csv)")
        if path:
            from pathlib import Path
            export_dictionary_csv(self._collect_dictionary(), Path(path))

    def _collect_dictionary(self) -> dict[str, str]:
        result = {}
        for r in range(self.table.rowCount()):
            word_item = self.table.item(r, 0)
            label_item = self.table.item(r, 1)
            if word_item and word_item.text():
                label = label_item.text() if label_item else "カスタム"
                result[word_item.text()] = _LABEL_TO_CATEGORY.get(label, "CUSTOM")
        return result

    def _on_accept(self):
        enabled = {c for c, cb in self._cat_checks.items() if cb.isChecked()}
        enabled.add("CUSTOM")  # 辞書検出は常時有効
        self._config.enabled_categories = enabled
        self._config.custom_dictionary = self._collect_dictionary()
        self.accept()
```

- [ ] **Step 2: main_window.py にメニューを追加**

`MainWindow.__init__` の末尾に追加:

```python
        menu = self.menuBar().addMenu("ツール")
        settings_action = menu.addAction("設定...")
        settings_action.triggered.connect(self._open_settings)
```

メソッドを追加:

```python
    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        from ..config import save_config
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec() == SettingsDialog.Accepted:
            save_config(self._config)
```

- [ ] **Step 3: 手動確認**

Run: `.venv\Scripts\python -m privacyprotection.gui.app`
確認項目:
1. ツール→設定で画面が開き、カテゴリチェックと辞書テーブルが表示される
2. 辞書に語句を追加してOK→その語句がマスクされるようになる
3. カテゴリ（例: 地名）をOFF→地名が検出されなくなる
4. 再起動しても設定が保持される（`%APPDATA%/PrivacyProtection/config.json`）

- [ ] **Step 4: 既存テストが壊れていないことを確認**

Run: `.venv\Scripts\python -m pytest tests -v`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/gui src/privacyprotection/config.py
git commit -m "feat: add settings dialog with category toggles and dictionary editor"
```

---

### Task 20: ネットワーク不使用の静的検査とビルド

**Files:**
- Create: `tests/test_no_network.py`, `scripts/build.ps1`

**Interfaces:**
- Consumes: `src/` 全体
- Produces: ネットワークAPI不使用を保証するテスト、PyInstallerビルドスクリプト

- [ ] **Step 1: 失敗するテストを書く（既にパスする想定だが、検査の網が正しいことを先に確認）**

```python
# tests/test_no_network.py
"""設計書 9.1: 自社コードにネットワークAPI呼び出しが含まれないことの静的検査。"""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

FORBIDDEN = re.compile(
    r"^\s*(?:import|from)\s+(socket|urllib|requests|http\.client|httpx|aiohttp|ftplib|smtplib|telnetlib)\b",
    re.MULTILINE,
)

def test_no_network_imports_in_source():
    violations = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in FORBIDDEN.finditer(text):
            violations.append(f"{py.relative_to(SRC)}: {m.group().strip()}")
    assert violations == [], f"ネットワークAPIのimportを検出: {violations}"

def test_detection_of_forbidden_import_works():
    # 検査自体が機能していることを確認（自己テスト）
    assert FORBIDDEN.search("import socket")
    assert FORBIDDEN.search("from urllib import request")
    assert not FORBIDDEN.search("import json")
```

- [ ] **Step 2: テストを実行**

Run: `.venv\Scripts\python -m pytest tests/test_no_network.py -v`
Expected: PASS（違反があれば FAIL するのでその import を除去する）

- [ ] **Step 3: ビルドスクリプトを作成**

```powershell
# scripts/build.ps1
# PyInstaller one-folder ビルド（設計書 9.2）。リポジトリルートで実行する。
$ErrorActionPreference = "Stop"

# spaCy/GiNZAモデルを同梱するため collect-all を使う
.venv\Scripts\pyinstaller `
    --noconfirm --windowed --name PrivacyProtection `
    --collect-all ja_ginza `
    --collect-all ginza `
    --collect-all spacy `
    --collect-all sudachipy `
    --collect-all sudachidict_core `
    src/privacyprotection/gui/app.py

# 配布ZIPとSHA-256（設計書 9.2）
Compress-Archive -Force -Path dist\PrivacyProtection -DestinationPath dist\PrivacyProtection.zip
(Get-FileHash dist\PrivacyProtection.zip -Algorithm SHA256).Hash |
    Out-File -Encoding ascii dist\PrivacyProtection.zip.sha256
Write-Host "Build complete: dist\PrivacyProtection.zip"
```

- [ ] **Step 4: ビルドして手動確認**

Run: `powershell -ExecutionPolicy Bypass -File scripts/build.ps1`
確認項目:
1. `dist\PrivacyProtection\PrivacyProtection.exe` が起動する
2. ネットワークを切断した状態で .txt のマスク（NER含む）が動作する
3. `dist\PrivacyProtection.zip.sha256` が生成されている

PyInstallerで spaCy/GiNZA が起動しない場合は hidden-import の追加が必要（エラーメッセージの ModuleNotFoundError 名を `--hidden-import` に追加して再ビルド）。

- [ ] **Step 5: 全テスト実行とコミット**

Run: `.venv\Scripts\python -m pytest tests -v`
Expected: PASS（全件。ja_ginza 導入済み環境では test_ner.py 含む）

```bash
git add tests/test_no_network.py scripts/build.ps1
git commit -m "feat: add no-network static check and PyInstaller build script"
```

---

## セルフレビュー記録

- **仕様カバレッジ**: F-1〜F-13 → Task 2/3/5(検出), 9-12(形式), 13(フォルダ), 6(方式), 7-8(復元), 18(プレビュー), 14-15(レポート・即変換), 19(辞書・カテゴリ), 17(クリップボード)。N-1 → Task 20。N-2 → Task 20。N-4 → Task 17。N-6 → Task 15
- **設計書との差分**: プレビューのフォルダ一括タブ表示（設計書6.2）は初版では未実装（フォルダ一括＝即変換のみ）。設計書が「多数の場合は確認なしモードを促す」としており、初版スコープとして許容。必要なら追加タスクで対応
- **型整合性**: `Fragment.encoding` は Task 9 で言及し Task 15 で必須化 → Task 1 の models.py に最初から入れず、Task 9 実装時に `encoding: str | None = None` を追加する方針で統一
