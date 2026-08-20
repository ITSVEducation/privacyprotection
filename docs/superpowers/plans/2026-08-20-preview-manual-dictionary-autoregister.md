# プレビュー手動追加のカスタム辞書自動登録 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** プレビュー画面で「この内容でマスク実行」を押したとき、有効な手動追加検出（`source == "manual"`）を語句→種別のペアとしてカスタム辞書へ自動登録する（既存語句は上書きしない・サイレント）。

**Architecture:** 収集ロジックは `gui/preview_dialog.py` の QApplication 不要な純粋関数 `collect_manual_entries()` に置き、`PreviewDialog.accepted_manual_entries()` がそれへ委譲する。マージは `config.py` の純粋関数 `merge_new_dictionary_entries()` に置く。永続化は `MainWindow` が Accepted 後にヘルパー `_register_manual_entries()` で行う（ダイアログは config に依存しない）。

**Tech Stack:** Python 3.11+ / PySide6 / pytest。依存追加なし。

**Spec:** `docs/superpowers/specs/2026-08-20-preview-manual-dictionary-autoregister-design.md`

## Global Constraints

- `pyproject.toml` の依存ピン（`spacy>=3.4.4,<3.8.0`、`numpy<2`）に触れない。
- レポート・ログ・エラーメッセージに検出値そのものを載せない（登録はサイレント。件数も表示しない）。
- GUI 層は services/ と config のみに依存する（core のデータ型 `Detection` の参照は既存どおり許容）。
- テスト実行は `uv run pytest ...`。lint/format ツールは導入しない。
- GUI テストは QApplication を必要としない純粋関数のみユニットテストする（`tests/gui/test_preview_display_map.py` と同じ方針）。

---

### Task 1: `collect_manual_entries()` 純粋関数と `PreviewDialog.accepted_manual_entries()`

**Files:**
- Modify: `src/privacyprotection/gui/preview_dialog.py`
- Test: `tests/gui/test_preview_manual_entries.py`（新規）

**Interfaces:**
- Consumes: `privacyprotection.core.models.Detection`（既存 dataclass。フィールド: `text, category, start, end, source, enabled`）
- Produces:
  - `collect_manual_entries(detections: list[list[Detection]]) -> dict[str, str]` — モジュールレベル純粋関数。`enabled` かつ `source == "manual"` の検出から `{text: category}` を組み立てる。同じ語句が複数あれば最初に現れたものが勝つ（`setdefault`）。
  - `PreviewDialog.accepted_manual_entries(self) -> dict[str, str]` — `collect_manual_entries(self.detections)` を返すだけ。

- [ ] **Step 1: 失敗するテストを書く**

`tests/gui/test_preview_manual_entries.py` を新規作成:

```python
"""gui/preview_dialog.py の collect_manual_entries() 直接ユニットテスト。

GUI層は原則として手動確認だが、この関数は QApplication を必要としない純粋
関数であり、「マスク実行時に手動追加検出をカスタム辞書へ自動登録する」仕様の
対象選別（有効な manual のみ）を一手に担うためユニットテストする。
"""
from privacyprotection.core.models import Detection
from privacyprotection.gui.preview_dialog import collect_manual_entries


def _det(text, category="CUSTOM", source="manual", enabled=True, start=0):
    return Detection(text=text, category=category, start=start,
                     end=start + len(text), source=source, enabled=enabled)


def test_collects_enabled_manual_detections():
    dets = [[_det("プロジェクトX")], [_det("山田商事", category="ORG", start=5)]]
    assert collect_manual_entries(dets) == {
        "プロジェクトX": "CUSTOM", "山田商事": "ORG"}


def test_excludes_disabled_manual_detections():
    dets = [[_det("プロジェクトX", enabled=False)]]
    assert collect_manual_entries(dets) == {}


def test_excludes_non_manual_sources():
    dets = [[_det("山田太郎", category="PERSON", source="ner"),
             _det("03-1234-5678", category="PHONE", source="pattern", start=10),
             _det("既存語", source="dictionary", start=30)]]
    assert collect_manual_entries(dets) == {}


def test_first_occurrence_wins_for_same_text():
    # 「同じ語をまとめて扱う」ONで全出現追加した後、片方だけカテゴリ変更
    # された場合など。最初に現れたものを採用する。
    dets = [[_det("プロジェクトX"), _det("プロジェクトX", category="ORG", start=20)]]
    assert collect_manual_entries(dets) == {"プロジェクトX": "CUSTOM"}


def test_category_change_is_reflected():
    dets = [[_det("山田太郎", category="PERSON")]]
    assert collect_manual_entries(dets) == {"山田太郎": "PERSON"}


def test_empty_detections():
    assert collect_manual_entries([]) == {}
    assert collect_manual_entries([[], []]) == {}
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `uv run pytest tests/gui/test_preview_manual_entries.py -v`
Expected: FAIL（`ImportError: cannot import name 'collect_manual_entries'`）

- [ ] **Step 3: 最小実装を書く**

`src/privacyprotection/gui/preview_dialog.py` の `build_display_map()` の直前（モジュールレベル）に追加:

```python
def collect_manual_entries(
        detections: list[list[Detection]]) -> dict[str, str]:
    """有効な手動追加検出から辞書登録用の {語句: カテゴリ} を組み立てる。

    マスク実行確定時にカスタム辞書へ自動登録する対象の選別（設計書 §4.2）。
    対象は enabled かつ source == "manual" のみ。自動検出（pattern/ner/
    dictionary）を含めると、一度でも検出された語がすべて辞書に固定されて
    しまい、カテゴリOFFで検出を止める手段が効かなくなるため含めない。
    同じ語句が複数あれば最初に現れたものが勝つ。
    """
    entries: dict[str, str] = {}
    for dets in detections:
        for d in dets:
            if d.enabled and d.source == "manual":
                entries.setdefault(d.text, d.category)
    return entries
```

`PreviewDialog` クラスの末尾（`_add_all_occurrences` の後）にメソッドを追加:

```python
    def accepted_manual_entries(self) -> dict[str, str]:
        """有効な手動追加検出の {語句: カテゴリ}。呼び出し元が Accepted 後に
        カスタム辞書へ登録するために使う（永続化はダイアログの責務外）。"""
        return collect_manual_entries(self.detections)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/gui/test_preview_manual_entries.py -v`
Expected: 6件すべて PASS

- [ ] **Step 5: 既存テストの回帰確認とコミット**

Run: `uv run pytest tests/ -v`
Expected: 全件 PASS

```bash
git add tests/gui/test_preview_manual_entries.py src/privacyprotection/gui/preview_dialog.py
git commit -m "feat: プレビューの有効な手動追加検出を辞書登録用に収集する"
```

---

### Task 2: `merge_new_dictionary_entries()`（config.py）

**Files:**
- Modify: `src/privacyprotection/config.py`
- Test: `tests/test_config.py`（既存ファイルに追記）

**Interfaces:**
- Consumes: なし（純粋関数）
- Produces: `merge_new_dictionary_entries(existing: dict[str, str], new: dict[str, str]) -> dict[str, str]` — `existing` に**無い**キーだけを抜き出して返す。`existing` は変更しない。呼び出し元は戻り値が空でなければ `existing.update(戻り値)` して保存する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py` の末尾に追加:

```python
def test_merge_new_dictionary_entries_adds_only_new_words():
    from privacyprotection.config import merge_new_dictionary_entries
    existing = {"山田商事": "ORG"}
    new = {"山田商事": "CUSTOM", "プロジェクトX": "CUSTOM"}
    added = merge_new_dictionary_entries(existing, new)
    # 既存語句は上書きしない（既存優先）。新規語句だけが返る。
    assert added == {"プロジェクトX": "CUSTOM"}
    # 引数の existing 自体は変更しない
    assert existing == {"山田商事": "ORG"}


def test_merge_new_dictionary_entries_empty_cases():
    from privacyprotection.config import merge_new_dictionary_entries
    assert merge_new_dictionary_entries({}, {"a": "CUSTOM"}) == {"a": "CUSTOM"}
    assert merge_new_dictionary_entries({"a": "CUSTOM"}, {}) == {}
    assert merge_new_dictionary_entries({}, {}) == {}
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `uv run pytest tests/test_config.py -v -k merge_new_dictionary`
Expected: FAIL（`ImportError: cannot import name 'merge_new_dictionary_entries'`）

- [ ] **Step 3: 最小実装を書く**

`src/privacyprotection/config.py` の `import_dictionary_csv()` の直前に追加:

```python
def merge_new_dictionary_entries(
        existing: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """`existing` に無い語句だけを `new` から抜き出して返す（既存優先）。

    プレビューで手動追加した検出のカスタム辞書への自動登録に使う。既存
    エントリを上書きすると、ユーザーが設定画面で意図して割り当てた種別が
    ドラッグ追加の既定カテゴリ（CUSTOM）で黙って塗り替わるため、追加のみ。
    """
    return {w: c for w, c in new.items() if w not in existing}
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/test_config.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add tests/test_config.py src/privacyprotection/config.py
git commit -m "feat: カスタム辞書へ新規語句のみをマージする関数を追加"
```

---

### Task 3: MainWindow への配線とドキュメント反映

**Files:**
- Modify: `src/privacyprotection/gui/main_window.py`
- Modify: `docs/04-ui-and-operations.md`

**Interfaces:**
- Consumes:
  - Task 1 の `PreviewDialog.accepted_manual_entries() -> dict[str, str]`
  - Task 2 の `merge_new_dictionary_entries(existing, new) -> dict[str, str]`
  - 既存の `save_config(cfg: AppConfig) -> None`（`..config`。main_window は import 済み）
- Produces: `MainWindow._register_manual_entries(entries: dict[str, str]) -> None`

GUI 層の配線であり QApplication が必要なため自動テストは書かない（本プロジェクトの GUI テスト方針）。ロジックは Task 1・2 で網羅済み。動作確認は手動で行う。

- [ ] **Step 1: `merge_new_dictionary_entries` を import する**

`src/privacyprotection/gui/main_window.py` の import 行を変更:

```python
from ..config import (
    default_config_path, load_config, merge_new_dictionary_entries,
    save_config,
)
```

- [ ] **Step 2: `_register_manual_entries` ヘルパーを追加する**

`_mask_one` の直後にメソッドを追加:

```python
    def _register_manual_entries(self, entries: dict[str, str]) -> None:
        """プレビューで手動追加された語句をカスタム辞書へ自動登録する。

        既存語句は上書きしない（設定画面で意図して割り当てた種別を守る）。
        通知はしない: 登録内容は設定画面のカスタム辞書タブでいつでも確認・
        削除できる。closeEvent 任せにせず即保存する（異常終了でも残るように）。
        """
        added = merge_new_dictionary_entries(
            self._config.custom_dictionary, entries)
        if added:
            self._config.custom_dictionary.update(added)
            save_config(self._config)
```

- [ ] **Step 3: `_mask_one` に配線する**

`_mask_one` のプレビュー分岐を変更（Accepted 後に登録を挟む）:

```python
    def _mask_one(self, pipeline: Pipeline, p: Path, fragments):
        frags, dets = pipeline.analyze_file(p, fragments=fragments)
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog(frags, dets, parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return
            self._register_manual_entries(dlg.accepted_manual_entries())
        _, report = pipeline.mask_file(p, frags, dets)
        self._show_report_text(self._single_report(report))
```

- [ ] **Step 4: `_mask_clipboard` に配線する**

`_mask_clipboard` のプレビュー分岐に同じ1行を追加:

```python
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog([frag], [dets], parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return  # キャンセル時はクリップボードを変更しない
            # PreviewDialog は [dets] を in-place 編集するため、確定後の dets が
            # そのまま編集結果（追加/無効化/カテゴリ変更を反映）になっている。
            self._register_manual_entries(dlg.accepted_manual_entries())
```

（既存コメントは残し、その直後に呼び出しを足すだけ。）

- [ ] **Step 5: 全テスト実行**

Run: `uv run pytest tests/ -v`
Expected: 全件 PASS

- [ ] **Step 6: 手動動作確認（GUI）**

Run: `uv run python -m privacyprotection.gui.app`

1. 適当なテキストをコピーし Ctrl+V → プレビューで任意の語をドラッグ追加 → OK。
2. `%APPDATA%/PrivacyProtection/config.json` の `custom_dictionary` にその語が
   `"CUSTOM"` で入っていることを確認する。
3. もう一度同じテキストで Ctrl+V → 追加した語が最初から検出（辞書由来）されて
   いることを確認する。
4. プレビューを Cancel した場合は登録されないことを確認する。

- [ ] **Step 7: docs を更新する**

`docs/04-ui-and-operations.md` §4.2 の「左ペインの直接操作」節の箇条書き（「手動追加は常に…」の直後）に追加:

```markdown
- 確定（マスク実行）時、有効な手動追加検出は語句＋種別でカスタム辞書（§4.3）へ自動登録される。
  既存語句は上書きしない。通知は出さず、登録内容は設定画面のカスタム辞書タブで確認・削除する。
  自動検出（パターン/辞書/NER）由来やチェックを外した検出は登録しない。キャンセル時も登録しない。
```

- [ ] **Step 8: コミット**

```bash
git add src/privacyprotection/gui/main_window.py docs/04-ui-and-operations.md
git commit -m "feat: マスク実行時に手動追加検出をカスタム辞書へ自動登録する"
```

---

### Task 4: 作業ドキュメントの蒸留・削除

**Files:**
- Delete: `docs/superpowers/specs/2026-08-20-preview-manual-dictionary-autoregister-design.md`
- Delete: `docs/superpowers/plans/2026-08-20-preview-manual-dictionary-autoregister.md`

**Interfaces:** なし（AGENTS.md のドキュメント運用ルールに従う後始末）

- [ ] **Step 1: 設計の結論が docs/ に反映済みであることを確認する**

Task 3 Step 7 で `docs/04-ui-and-operations.md` に反映済み。それ以外に反映すべき
結論はない（実装手順・チェックボックスは反映しない）。

- [ ] **Step 2: 作業ドキュメントを削除してコミット**

```bash
git rm docs/superpowers/specs/2026-08-20-preview-manual-dictionary-autoregister-design.md docs/superpowers/plans/2026-08-20-preview-manual-dictionary-autoregister.md
git commit -m "docs: 辞書自動登録の作業ドキュメントを削除（docs/04へ蒸留済み）"
```
