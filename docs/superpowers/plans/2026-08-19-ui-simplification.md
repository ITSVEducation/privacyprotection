# メイン画面UI簡素化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** メイン画面を「ドロップ or Ctrl+V だけで正しく使える」構成にし、モード切替を意図の自動判定＋確認ダイアログに置き換え、上級者向け設定を折りたたみパネルへ集約する。

**Architecture:** 判定ロジックは純関数として `services/intent.py` に置きユニットテストを付ける。GUI は `services/` のみに依存する既存の3層構造を維持し、`Pipeline` に断片再利用（`read_fragments`）とフォルダ復元（`restore_folder`）の公開APIを追加する。GUI 側は折りたたみパネル（`gui/advanced_panel.py`）と意図判定フローの配線のみ。

**Tech Stack:** Python 3.11+ / PySide6 / pytest / uv

**Spec:** `docs/superpowers/specs/2026-08-19-ui-simplification-design.md`

## Global Constraints

- 依存関係は uv で管理。テスト実行は `uv run pytest tests/ -v`（`pyproject.toml` の `spacy>=3.4.4,<3.8.0` / `numpy<2` ピンには触れない）
- GUI（`gui/`）は `services/` のみを import する。`core/` `handlers/` を直接 import しない
- レポート・ダイアログ・ログ・例外メッセージに検出値そのものを載せない（カテゴリ名・件数・ファイル名・トークン名のみ可）
- `Restorer` は完全一致のみ。バッチ全体で `Masker` は1つ。既存の不変条件テストを弱めない
- コミットメッセージは日本語（既存の履歴に倣う）。lint/format ツールは導入しない
- GUI 層は手動確認中心（自動テストは追加しない）。テスト可能なロジックは services 層に置く

---

### Task 1: AppConfig に `action_mode` / `advanced_expanded` を追加

**Files:**
- Modify: `src/privacyprotection/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig.action_mode: str = "auto"`（`"auto" | "mask" | "restore"`）、`AppConfig.advanced_expanded: bool = False`。`load_config` / `save_config` が両フィールドを往復し、キーがない既存 config.json では既定値になる。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py` に追記（既存テストのスタイルに合わせる。既存の import をそのまま使う）:

```python
def test_new_ui_fields_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = AppConfig(action_mode="restore", advanced_expanded=True)
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.action_mode == "restore"
    assert loaded.advanced_expanded is True


def test_new_ui_fields_default_when_missing(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"mask_mode": "token"}', encoding="utf-8")
    loaded = load_config(p)
    assert loaded.action_mode == "auto"
    assert loaded.advanced_expanded is False
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`TypeError: unexpected keyword argument 'action_mode'`）

- [ ] **Step 3: 実装**

`src/privacyprotection/config.py` の `AppConfig` にフィールドを追加:

```python
@dataclass
class AppConfig:
    enabled_categories: set[str] = field(default_factory=lambda: set(ALL_CATEGORIES))
    custom_dictionary: dict[str, str] = field(default_factory=dict)
    output_dir: str | None = None
    skip_preview: bool = False
    mask_mode: str = "token"
    action_mode: str = "auto"          # "auto" | "mask" | "restore"
    advanced_expanded: bool = False    # 詳細設定パネルの開閉状態
```

`load_config` の戻り値に追加:

```python
        action_mode=data.get("action_mode", "auto"),
        advanced_expanded=bool(data.get("advanced_expanded", False)),
```

`save_config` の `data` に追加:

```python
        "action_mode": cfg.action_mode,
        "advanced_expanded": cfg.advanced_expanded,
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/config.py tests/test_config.py
git commit -m "feat: AppConfig に action_mode / advanced_expanded を追加"
```

---

### Task 2: 意図判定の純関数 `services/intent.py`

**Files:**
- Create: `src/privacyprotection/services/intent.py`
- Test: `tests/services/test_intent.py`

**Interfaces:**
- Consumes: `core.models.TOKEN_RE`、`handlers.folder_walker.MASKED_STEM_RE`（services→core / services→handlers は既存の許可された依存方向）
- Produces:
  - `Intent`（Enum: `Intent.MASK` / `Intent.RESTORE`）
  - `FOLDER_MAPPING_NAME = "_folder.pmap.csv"`
  - `decide_text_intent(text: str, action_mode: str = "auto") -> Intent`
  - `decide_file_intent(path: Path, fragment_texts: list[str], action_mode: str = "auto") -> Intent`
  - `decide_folder_intent(root: Path, action_mode: str = "auto") -> Intent`

- [ ] **Step 1: 失敗するテストを書く**

`tests/services/test_intent.py` を新規作成:

```python
from pathlib import Path

from privacyprotection.services.intent import (
    FOLDER_MAPPING_NAME, Intent, decide_file_intent, decide_folder_intent,
    decide_text_intent,
)


# --- テキスト ---------------------------------------------------------

def test_text_without_token_is_mask():
    assert decide_text_intent("山田太郎です") is Intent.MASK


def test_text_with_token_is_restore():
    assert decide_text_intent("【人名_1】様、お世話になります") is Intent.RESTORE


def test_text_action_mode_overrides_auto():
    assert decide_text_intent("【人名_1】", action_mode="mask") is Intent.MASK
    assert decide_text_intent("平文", action_mode="restore") is Intent.RESTORE


# --- ファイル ---------------------------------------------------------

def test_file_with_own_sidecar_mapping_is_restore(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / "memo_masked.txt.pmap.csv").write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["x"]) is Intent.RESTORE


def test_masked_named_file_with_folder_mapping_is_restore(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["x"]) is Intent.RESTORE


def test_plain_file_next_to_folder_mapping_is_mask(tmp_path):
    # フォルダ一括処理は既定で元ファイルと同じフォルダに出力するため、
    # 元ファイルの隣にも _folder.pmap.csv が存在する。マスク済みの命名
    # （*_masked）でないファイルまで復元と誤判定してはいけない。
    f = tmp_path / "memo.txt"
    f.write_text("平文", encoding="utf-8")
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["平文"]) is Intent.MASK


def test_file_with_token_in_fragments_is_restore(tmp_path):
    f = tmp_path / "downloaded.txt"
    f.write_text("x", encoding="utf-8")
    assert decide_file_intent(f, ["こんにちは", "【電話_2】まで"]) is Intent.RESTORE


def test_plain_file_is_mask(tmp_path):
    f = tmp_path / "memo.txt"
    f.write_text("平文", encoding="utf-8")
    assert decide_file_intent(f, ["平文"]) is Intent.MASK


def test_file_action_mode_overrides_auto(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / "memo_masked.txt.pmap.csv").write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["【人名_1】"], action_mode="mask") is Intent.MASK
    assert decide_file_intent(tmp_path / "plain.txt", ["平文"],
                              action_mode="restore") is Intent.RESTORE


# --- フォルダ ---------------------------------------------------------

def test_folder_with_mapping_is_restore(tmp_path):
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_folder_intent(tmp_path) is Intent.RESTORE


def test_folder_without_mapping_is_mask(tmp_path):
    assert decide_folder_intent(tmp_path) is Intent.MASK


def test_folder_action_mode_overrides_auto(tmp_path):
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_folder_intent(tmp_path, action_mode="mask") is Intent.MASK
    assert decide_folder_intent(tmp_path, action_mode="restore") is Intent.RESTORE
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/services/test_intent.py -v`
Expected: FAIL（`ModuleNotFoundError: privacyprotection.services.intent`）

- [ ] **Step 3: 実装**

`src/privacyprotection/services/intent.py` を新規作成:

```python
"""入力からマスク/復元の意図を判定する純関数（設計書 04 §4.1）。

判定は決定的なルールのみで行う。GUI は復元と判定された場合に必ず確認
ダイアログを挟むため、誤判定の影響は「1回余計に確認される」に留まる。
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable

from ..core.models import TOKEN_RE
from ..handlers.folder_walker import MASKED_STEM_RE

FOLDER_MAPPING_NAME = "_folder.pmap.csv"


class Intent(Enum):
    MASK = "mask"
    RESTORE = "restore"


def _contains_tokens(texts: Iterable[str]) -> bool:
    return any(TOKEN_RE.search(t) for t in texts)


def _forced(action_mode: str) -> Intent | None:
    if action_mode == "mask":
        return Intent.MASK
    if action_mode == "restore":
        return Intent.RESTORE
    return None


def decide_text_intent(text: str, action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    return Intent.RESTORE if _contains_tokens([text]) else Intent.MASK


def decide_file_intent(path: Path, fragment_texts: list[str],
                       action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    if (path.parent / (path.name + ".pmap.csv")).exists():
        return Intent.RESTORE
    # _folder.pmap.csv は元ファイルの隣にも存在し得る（フォルダ一括処理は
    # 既定で同じフォルダに出力するため）。本アプリのマスク済み命名
    # （*_masked / *_masked(N)）のファイルに限って復元とみなす。
    if (MASKED_STEM_RE.match(path.stem)
            and (path.parent / FOLDER_MAPPING_NAME).exists()):
        return Intent.RESTORE
    if _contains_tokens(fragment_texts):
        return Intent.RESTORE
    return Intent.MASK


def decide_folder_intent(root: Path, action_mode: str = "auto") -> Intent:
    forced = _forced(action_mode)
    if forced is not None:
        return forced
    if (root / FOLDER_MAPPING_NAME).exists():
        return Intent.RESTORE
    return Intent.MASK
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/services/test_intent.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/services/intent.py tests/services/test_intent.py
git commit -m "feat: マスク/復元の意図を自動判定する services/intent.py を追加"
```

---

### Task 3: Pipeline に `read_fragments` と `analyze_file` の断片再利用を追加

**Files:**
- Modify: `src/privacyprotection/services/pipeline.py`（`analyze_file` 付近、164行目前後）
- Test: `tests/services/test_pipeline.py`

**Interfaces:**
- Produces:
  - `Pipeline.read_fragments(path: Path) -> list[Fragment]` — handler で断片を読むだけ（検出しない）。未対応拡張子は `ValueError`
  - `Pipeline.analyze_file(path: Path, fragments: list[Fragment] | None = None)` — `fragments` を渡すと再読み込みせずそのまま検出に使う（GUI が意図判定で読んだ断片を再利用するため）

- [ ] **Step 1: 失敗するテストを書く**

`tests/services/test_pipeline.py` に追記（既存の `make_pipeline` を使う）:

```python
def test_read_fragments_reads_without_detection(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline()
    frags = pl.read_fragments(src)
    assert [f.text for f in frags] == ["山田太郎"]


def test_read_fragments_rejects_unsupported_extension(tmp_path):
    src = tmp_path / "memo.exe"
    src.write_text("x", encoding="utf-8")
    pl = make_pipeline()
    with pytest.raises(ValueError):
        pl.read_fragments(src)


def test_analyze_file_reuses_given_fragments(tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("山田太郎", encoding="utf-8")
    pl = make_pipeline()
    frags = pl.read_fragments(src)
    src.unlink()  # 再読み込みしていればここで失敗する
    frags2, dets = pl.analyze_file(src, fragments=frags)
    assert frags2 is frags
    assert dets[0][0].category == "PERSON"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/services/test_pipeline.py -v -k "read_fragments or reuses"`
Expected: FAIL（`AttributeError: 'Pipeline' object has no attribute 'read_fragments'`）

- [ ] **Step 3: 実装**

`pipeline.py` の `analyze_file` を次に置き換え、直前に `read_fragments` を追加:

```python
    def read_fragments(self, path: Path) -> list[Fragment]:
        """ファイルからテキスト断片を読むだけの公開API（検出はしない）。

        GUI が意図の自動判定（services/intent.py）のために断片テキストを
        必要とするが、GUI は handlers/ を直接 import できないため、
        Pipeline の公開APIとして提供する。読んだ断片は analyze_file の
        `fragments` 引数に渡して再利用できる（二重読み込みの回避）。
        """
        handler = get_handler(path)
        if handler is None:
            raise ValueError(f"未対応の拡張子です: {path.suffix}")
        return handler.read_fragments(path)

    def analyze_file(self, path: Path,
                     fragments: list[Fragment] | None = None):
        if fragments is None:
            fragments = self.read_fragments(path)
        detections = [self._detector.detect(f.text) for f in fragments]
        return fragments, detections
```

- [ ] **Step 4: テストが通ることを確認（既存テストの回帰も見る）**

Run: `uv run pytest tests/services/ -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/services/pipeline.py tests/services/test_pipeline.py
git commit -m "feat: Pipeline.read_fragments を追加し analyze_file で断片を再利用可能にする"
```

---

### Task 4: `Pipeline.restore_folder`（フォルダ一括復元）

**Files:**
- Modify: `src/privacyprotection/services/pipeline.py`（`restore_file` の後ろ）
- Test: `tests/services/test_pipeline.py`

**Interfaces:**
- Consumes: `restore_file`（既存）、`MASKED_STEM_RE` / `get_handler`（既存 import 済み）、`intent.FOLDER_MAPPING_NAME`
- Produces: `Pipeline.restore_folder(root: Path, progress: Callable[[int, int, Path], None] | None = None) -> tuple[int, list[str]]` — 戻り値は（復元成功件数, 警告文字列のリスト）。警告にはファイル名・件数のみを含め、検出値・トークンの中身は含めない。`MaskWorker` が `progress` を自動配線できるようシグネチャを `mask_folder` と揃える。

- [ ] **Step 1: 失敗するテストを書く**

`tests/services/test_pipeline.py` に追記:

```python
def test_restore_folder_roundtrip(tmp_path):
    (tmp_path / "a.txt").write_text("山田太郎です", encoding="utf-8")
    (tmp_path / "b.txt").write_text("佐藤花子さん", encoding="utf-8")
    pl = make_pipeline()
    pl.mask_folder(tmp_path)

    restored, warnings = pl.restore_folder(tmp_path)
    assert restored == 2
    assert warnings == []
    assert (tmp_path / "a_restored.txt").read_text(encoding="utf-8") == "山田太郎です"
    assert (tmp_path / "b_restored.txt").read_text(encoding="utf-8") == "佐藤花子さん"


def test_restore_folder_reports_failures_without_values(tmp_path):
    # マスク済み命名だが対応表がどこにもない → 1件の警告（値は含まない）
    (tmp_path / "orphan_masked.txt").write_text("【人名_9】", encoding="utf-8")
    pl = make_pipeline()
    restored, warnings = pl.restore_folder(tmp_path)
    assert restored == 0
    assert len(warnings) == 1
    assert "orphan_masked.txt" in warnings[0]
    assert "【人名_9】" not in warnings[0]


def test_restore_folder_skips_non_masked_files(tmp_path):
    (tmp_path / "plain.txt").write_text("平文", encoding="utf-8")
    pl = make_pipeline()
    restored, warnings = pl.restore_folder(tmp_path)
    assert restored == 0
    assert warnings == []
    assert not (tmp_path / "plain_restored.txt").exists()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/services/test_pipeline.py -v -k restore_folder`
Expected: FAIL（`AttributeError: 'Pipeline' object has no attribute 'restore_folder'`）

- [ ] **Step 3: 実装**

`pipeline.py` の先頭付近に import を追加:

```python
from .intent import FOLDER_MAPPING_NAME
```

`restore_file` の後ろにメソッドを追加:

```python
    def restore_folder(self, root: Path,
                       progress: Callable[[int, int, Path], None] | None = None
                       ) -> tuple[int, list[str]]:
        """フォルダ内の本アプリ出力（*_masked / *_masked(N)）を一括復元する。

        対応表はフォルダ共有の `_folder.pmap.csv` があればそれを使い、
        なければファイルごとのサイドカー（restore_file の既定探索）に
        委ねる。戻り値の警告リストにはファイル名・件数のみを載せ、
        検出値やトークンの中身は含めない（不変条件）。
        """
        shared = root / FOLDER_MAPPING_NAME
        mapping_path = shared if shared.exists() else None
        targets = [p for p in sorted(root.rglob("*"))
                   if p.is_file() and MASKED_STEM_RE.match(p.stem)
                   and get_handler(p) is not None]
        restored = 0
        warnings: list[str] = []
        total = len(targets)
        for i, path in enumerate(targets, start=1):
            if progress:
                progress(i, total, path)
            try:
                _, result = self.restore_file(path, mapping_path=mapping_path)
                restored += 1
                if result.unknown_tokens:
                    warnings.append(
                        f"{path.name}: 未知トークン {len(result.unknown_tokens)} 件が"
                        "そのまま残っています")
            except Exception as exc:
                warnings.append(
                    f"{path.name}: {type(exc).__name__}: 処理できませんでした")
        return restored, warnings
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/services/ -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/services/pipeline.py tests/services/test_pipeline.py
git commit -m "feat: Pipeline.restore_folder でフォルダ一括復元を追加"
```

---

### Task 5: 折りたたみ式の詳細設定パネル `gui/advanced_panel.py`

**Files:**
- Create: `src/privacyprotection/gui/advanced_panel.py`

**Interfaces:**
- Consumes: `AppConfig`（`action_mode` / `mask_mode` / `skip_preview` / `advanced_expanded`）
- Produces: `AdvancedPanel(QWidget)`:
  - `AdvancedPanel(config: AppConfig, parent=None)` — 初期状態は config から反映
  - `action_mode() -> str`（`"auto" | "mask" | "restore"`）
  - `mask_mode() -> str`（`"token" | "redact"`）
  - `skip_preview() -> bool`
  - `is_expanded() -> bool`
  - `settings_requested: Signal()` / `dictionary_requested: Signal()` — 「設定…」「辞書を編集…」ボタン押下
  - ヘッダーは畳んだ状態でも現在の設定を要約表示し、既定（自動判定・可逆・確認あり）から変えている場合は強調色にする

- [ ] **Step 1: 実装**

`src/privacyprotection/gui/advanced_panel.py` を新規作成:

```python
"""折りたたみ式の詳細設定パネル（設計書 04 §4.1）。

Qt 標準に disclosure ウィジェットがないため、QToolButton（矢印）＋
コンテナ widget の表示切替で実装する。ヘッダーは畳んだ状態でも現在の
動作を要約表示し、既定（自動判定・可逆・確認あり）から変えている場合は
強調色にして「不可逆のまま気づかない」事故を防ぐ。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QToolButton, QVBoxLayout, QWidget,
)

from ..config import AppConfig

# (表示ラベル, action_mode値) — インデックスが QComboBox の並びと対応する
_ACTION_ITEMS = [("自動判定", "auto"), ("常にマスク", "mask"), ("常に復元", "restore")]
_ACCENT_STYLE = "color: #b06000; font-weight: bold;"


class AdvancedPanel(QWidget):
    settings_requested = Signal()
    dictionary_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.RightArrow)
        self._toggle.setAutoRaise(True)
        self._toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("動作:"))
        self._action_combo = QComboBox()
        for label, _value in _ACTION_ITEMS:
            self._action_combo.addItem(label)
        values = [v for _, v in _ACTION_ITEMS]
        self._action_combo.setCurrentIndex(
            values.index(config.action_mode)
            if config.action_mode in values else 0)
        self._action_combo.currentIndexChanged.connect(self._update_summary)
        action_row.addWidget(self._action_combo)
        action_row.addStretch()
        body_layout.addLayout(action_row)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("方式:"))
        self._token_radio = QRadioButton("可逆（トークン）")
        self._redact_radio = QRadioButton("不可逆（塗りつぶし）")
        (self._token_radio if config.mask_mode == "token"
         else self._redact_radio).setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self._token_radio)
        group.addButton(self._redact_radio)
        self._token_radio.toggled.connect(self._update_summary)
        method_row.addWidget(self._token_radio)
        method_row.addWidget(self._redact_radio)
        method_row.addStretch()
        body_layout.addLayout(method_row)

        self._skip_preview = QCheckBox("確認なしで即変換")
        self._skip_preview.setChecked(config.skip_preview)
        self._skip_preview.toggled.connect(self._update_summary)
        body_layout.addWidget(self._skip_preview)

        buttons_row = QHBoxLayout()
        self._settings_btn = QPushButton("設定…")
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        self._dictionary_btn = QPushButton("辞書を編集…")
        self._dictionary_btn.setToolTip(
            "必ずマスクしたい社名・製品名・氏名などを登録します")
        self._dictionary_btn.clicked.connect(self.dictionary_requested.emit)
        buttons_row.addWidget(self._settings_btn)
        buttons_row.addWidget(self._dictionary_btn)
        buttons_row.addStretch()
        body_layout.addLayout(buttons_row)

        layout.addWidget(self._body)
        self._toggle.setChecked(config.advanced_expanded)
        self._body.setVisible(config.advanced_expanded)
        self._update_summary()

    # --- 公開アクセサ（MainWindow が config 保存・pipeline 構築に使う） ---
    def action_mode(self) -> str:
        return _ACTION_ITEMS[self._action_combo.currentIndex()][1]

    def mask_mode(self) -> str:
        return "token" if self._token_radio.isChecked() else "redact"

    def skip_preview(self) -> bool:
        return self._skip_preview.isChecked()

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    # --- 内部 -----------------------------------------------------------
    def _on_toggled(self, expanded: bool) -> None:
        self._body.setVisible(expanded)
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def _update_summary(self, *_args) -> None:
        parts = []
        if self.action_mode() == "mask":
            parts.append("常にマスク")
        elif self.action_mode() == "restore":
            parts.append("常に復元")
        parts.append("可逆マスク" if self.mask_mode() == "token" else "不可逆マスク")
        parts.append("即変換" if self.skip_preview() else "確認あり")
        self._toggle.setText(f"詳細設定（{'・'.join(parts)}）")
        is_default = (self.action_mode() == "auto"
                      and self.mask_mode() == "token"
                      and not self.skip_preview())
        self._toggle.setStyleSheet("" if is_default else _ACCENT_STYLE)
```

- [ ] **Step 2: 回帰がないことを確認**

Run: `uv run pytest tests/ -v`
Expected: 全件 PASS（このタスクは新規ファイルのみで既存に影響しない）

- [ ] **Step 3: コミット**

```bash
git add src/privacyprotection/gui/advanced_panel.py
git commit -m "feat: 折りたたみ式の詳細設定パネル AdvancedPanel を追加"
```

---

### Task 6: メイン画面の再構成と意図判定フローの配線

**Files:**
- Modify: `src/privacyprotection/gui/main_window.py`（全体を下記方針で書き換え）

**Interfaces:**
- Consumes: `AdvancedPanel`（Task 5）、`services.intent` の `Intent` / `decide_text_intent` / `decide_file_intent` / `decide_folder_intent`（Task 2）、`Pipeline.read_fragments` / `analyze_file(fragments=...)`（Task 3）、`Pipeline.restore_folder`（Task 4）
- Produces: なし（最終消費者）

- [ ] **Step 1: 画面構成の変更**

`main_window.py` の `MainWindow.__init__` を変更:

1. モード切替ラジオ（`mask_radio`/`restore_radio`）、方式ラジオ（`token_radio`/`redact_radio`）、`skip_preview` チェック、`settings_row` を**削除**
2. レイアウトを「ドロップゾーン（stretch=1）→ クリップボードボタン → `AdvancedPanel` → 進捗バー → 注意書き」の順にする
3. ドロップゾーンの案内文を変更:

```python
        super().__init__("ここにファイル／フォルダをドロップ\n"
                         "（クリックで選択 ／ Ctrl+V でクリップボードを処理）")
```

4. クリップボードボタンのラベルを固定にし、モード連動を削除:

```python
        self.clipboard_btn = QPushButton("クリップボードを処理してコピー")
        self.clipboard_btn.clicked.connect(self._clipboard_action)
```

（`_update_clipboard_button_label` メソッドと `mask_radio.toggled` の接続は削除）

5. パネルの生成と接続:

```python
        from .advanced_panel import AdvancedPanel
        self.advanced_panel = AdvancedPanel(self._config)
        self.advanced_panel.settings_requested.connect(self._open_settings)
        self.advanced_panel.dictionary_requested.connect(self._open_dictionary)
        layout.addWidget(self.advanced_panel)
```

6. Ctrl+V ショートカットを登録（import: `from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut`）:

```python
        # Ctrl+V はクリップボードボタンと同じ処理を起動する（設計書 04 §4.1）。
        # 参照を self に保持するのはメニュー/アクションと同じ理由（GC対策）。
        self._paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        self._paste_shortcut.activated.connect(self._clipboard_action)
```

- [ ] **Step 2: pipeline 構築と設定保存をパネル参照に切り替え**

```python
    def _build_pipeline(self) -> Pipeline:
        # mask_mode はパネルの現在の選択を優先する（self._config は起動時の
        # スナップショットで、closeEvent で保存するまで更新されない）。
        cfg = replace(self._config, mask_mode=self.advanced_panel.mask_mode())
        return Pipeline.from_config(cfg)

    def closeEvent(self, e):
        self._config.skip_preview = self.advanced_panel.skip_preview()
        self._config.mask_mode = self.advanced_panel.mask_mode()
        self._config.action_mode = self.advanced_panel.action_mode()
        self._config.advanced_expanded = self.advanced_panel.is_expanded()
        save_config(self._config)
        super().closeEvent(e)
```

`self.skip_preview.isChecked()` を参照している箇所（`_mask_paths` / `_mask_clipboard`）はすべて `self.advanced_panel.skip_preview()` に置き換える。

- [ ] **Step 3: 意図判定フローを配線**

復元確認の共通ヘルパを追加:

```python
    def _confirm_restore(self, subject: str) -> bool:
        """復元と自動判定したときの確認。いいえ→マスクにフォールバック。"""
        return QMessageBox.question(
            self, "復元の確認",
            f"マスク済み{subject}のようです。復元しますか？\n"
            "「いいえ」を選ぶとマスクします。",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes
```

`_handle_paths` を意図判定ベースに書き換え（`restore_radio` 参照を削除）:

```python
    def _handle_paths(self, paths: list[Path]):
        # --windowed ビルドでは stderr が存在しないため、スロット内の例外は
        # 必ずダイアログとして表示する（従来コメントの理由と同じ）。
        try:
            self._dispatch_paths(paths)
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

    def _dispatch_paths(self, paths: list[Path]):
        from ..services.intent import (
            Intent, decide_file_intent, decide_folder_intent)
        pipeline = self._build_pipeline()
        action_mode = self.advanced_panel.action_mode()

        if len(paths) == 1 and paths[0].is_dir():
            root = paths[0]
            intent = decide_folder_intent(root, action_mode)
            if intent is Intent.RESTORE and (
                    action_mode == "restore" or self._confirm_restore("フォルダ")):
                self._run_worker(pipeline.restore_folder, root,
                                 on_done=self._on_folder_restored)
            else:
                self._run_worker(pipeline.mask_folder, root,
                                 on_done=self._on_folder_masked)
            return

        for p in paths:
            try:
                fragments = pipeline.read_fragments(p)
                intent = decide_file_intent(
                    p, [f.text for f in fragments], action_mode)
                if intent is Intent.RESTORE and (
                        action_mode == "restore"
                        or self._confirm_restore("ファイル")):
                    self._restore_one(pipeline, p)
                else:
                    self._mask_one(pipeline, p, fragments)
            except Exception as exc:
                QMessageBox.warning(
                    self, "エラー",
                    f"{p.name}: {type(exc).__name__}: 処理できませんでした")

    def _mask_one(self, pipeline: Pipeline, p: Path, fragments):
        frags, dets = pipeline.analyze_file(p, fragments=fragments)
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog(frags, dets, parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return
        out, report = pipeline.mask_file(p, frags, dets)
        self._show_report_text(self._single_report(report))
```

既存の `_mask_paths` / `_restore_paths` メソッドは削除する（`_restore_one` と `_resolve_mapping_path` は現行のまま残す）。

クリップボード処理も意図判定ベースに:

```python
    def _clipboard_action(self):
        try:
            from ..services.intent import Intent, decide_text_intent
            text = QGuiApplication.clipboard().text()
            if not text:
                QMessageBox.information(self, "クリップボード", "テキストがありません")
                return
            action_mode = self.advanced_panel.action_mode()
            intent = decide_text_intent(text, action_mode)
            if intent is Intent.RESTORE and (
                    action_mode == "restore"
                    or self._confirm_restore("テキスト")):
                self._restore_clipboard()
            else:
                self._mask_clipboard()
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")
```

（`_mask_clipboard` / `_restore_clipboard` の中身は現行のまま。`_mask_clipboard` 内の `self.skip_preview.isChecked()` だけ `self.advanced_panel.skip_preview()` に置き換える。空テキスト判定は `_clipboard_action` に移したので、両メソッド先頭の空チェックは残しても害はないがそのままでよい）

- [ ] **Step 4: ワーカー完了ハンドラの一般化**

`_run_worker` に `on_done` キーワード引数を追加し、フォルダのマスク/復元で完了処理を分ける:

```python
    def _run_worker(self, fn, *args, on_done):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "処理中", "前の処理が完了するまでお待ちください")
            return
        self.progress.setVisible(True)
        self._set_controls_enabled(False)
        self._on_done = on_done
        self._worker = MaskWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_finished(self, result):
        self.progress.setVisible(False)
        self._set_controls_enabled(True)
        self._on_done(result)

    def _on_folder_masked(self, result):
        batch, mapping_path = result
        self._show_report_text(batch.render_text())

    def _on_folder_restored(self, result):
        restored, warnings = result
        msg = f"{restored} 件のファイルを復元しました。"
        if warnings:
            msg += "\n\n" + "\n".join(warnings)
        QMessageBox.information(self, "復元完了", msg)
```

`_set_controls_enabled` にパネルとショートカットも含める:

```python
    def _set_controls_enabled(self, enabled: bool) -> None:
        self.drop_zone.setEnabled(enabled)
        self.clipboard_btn.setEnabled(enabled)
        self.advanced_panel.setEnabled(enabled)
        self._paste_shortcut.setEnabled(enabled)
```

- [ ] **Step 5: 回帰テストと手動確認**

Run: `uv run pytest tests/ -v`
Expected: 全件 PASS

Run: `uv run python -m privacyprotection.gui.app`

手動確認チェックリスト:
1. 起動直後: ドロップゾーンが主役、詳細設定は畳まれ「▸ 詳細設定（可逆マスク・確認あり）」表示
2. 平文の .txt をドロップ → プレビュー → 確定 → レポート（従来どおり）
3. 出力された `*_masked.txt` をドロップ → 「マスク済みファイルのようです。復元しますか？」→ はい → 復元完了
4. 同ダイアログで「いいえ」→ プレビュー（マスク経路）に進む
5. 平文をコピーして Ctrl+V → プレビュー → クリップボードがマスク文字列になる
6. マスク済みテキスト（`【人名_1】` を含む）をコピーして Ctrl+V → 復元確認 → 対応表選択 → 復元される
7. フォルダをドロップ → 一括マスク。その後同じフォルダをドロップ → 復元確認 → はい → 「N 件のファイルを復元しました」
8. 詳細設定を開き「不可逆」へ変更 → ヘッダーが「詳細設定（不可逆マスク・確認あり）」になり強調色になる
9. 「常に復元」へ変更 → 平文ファイルをドロップしても確認なしで復元経路（対応表選択ダイアログ）に入る
10. アプリを終了して再起動 → 詳細設定の内容・開閉状態が維持されている

- [ ] **Step 6: コミット**

```bash
git add src/privacyprotection/gui/main_window.py
git commit -m "feat: メイン画面を意図の自動判定＋詳細設定パネル構成に再編"
```

---

### Task 7: 設計書の更新と作業ドキュメントの蒸留

**Files:**
- Modify: `docs/04-ui-and-operations.md`（§4.1、§4.5 の関連行）
- Delete: `docs/superpowers/specs/2026-08-19-ui-simplification-design.md`
- Delete: `docs/superpowers/plans/2026-08-19-ui-simplification.md`

**Interfaces:** なし（ドキュメントのみ）

- [ ] **Step 1: `docs/04-ui-and-operations.md` §4.1 を現状に合わせて書き換え**

書き換えの要点（現在形で書く。経緯・日付は書かない）:

- 画面構成の箇条書きを「ドロップゾーン（主役、Ctrl+V の案内を含む）／クリップボードボタン（固定ラベル）／詳細設定の折りたたみパネル（動作・方式・即変換・設定/辞書ボタン、ヘッダーに現在設定を要約表示、既定から変更時は強調色）／進捗バー／常時注記」に更新
- 「モード切替: マスク／復元」の行を削除し、新しい節「意図の自動判定」を追加。設計書（スペック §2）の判定表をそのまま転記する:
  - テキスト: トークン（`【種別_n】`）を含めば復元を提案、なければマスク
  - ファイル: `<ファイル名>.pmap.csv` が隣にある／`*_masked` 命名かつ `_folder.pmap.csv` が隣にある／断片にトークンを含む、のいずれかで復元を提案
  - フォルダ: 直下に `_folder.pmap.csv` があれば一括復元を提案（対象は `*_masked` 命名のファイルのみ）、なければ一括マスク
  - 復元の提案は必ず確認ダイアログを挟み、「いいえ」でマスクにフォールバック
  - 詳細設定の「動作」（自動判定／常にマスク／常に復元）で判定を固定できる。固定時は確認ダイアログを出さない
- Ctrl+V ショートカットがクリップボードボタンと同じ処理であることを記載
- 設定の永続化に `action_mode` / `advanced_expanded` が加わったことを反映
- §4.5 の表の「フォルダ一括処理」行に、復元も同様にワーカースレッドで実行する旨を追記

- [ ] **Step 2: スクリーンショットの注記**

`docs/images/main-window.png` は旧UIのままになるため、画像を差し替えるまでの間、§4.1 の画像参照の直後に「（画像は更新前のもの）」等の注記は**付けない**。代わりに画像参照行を一時的に削除し、手動確認時に取得した新UIのスクリーンショットがあれば差し替える（なければ削除のままでよい。docs は現状と一致させる方が優先）。

- [ ] **Step 3: 作業ドキュメントを削除**

AGENTS.md の運用ルールどおり、結論を docs/ に蒸留したら元ファイルを削除する:

```bash
git rm docs/superpowers/specs/2026-08-19-ui-simplification-design.md
git rm docs/superpowers/plans/2026-08-19-ui-simplification.md
```

- [ ] **Step 4: 全テスト実行**

Run: `uv run pytest tests/ -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add docs/04-ui-and-operations.md
git commit -m "docs: メイン画面のUI簡素化を設計書に反映し、作業ドキュメントを蒸留・削除"
```
