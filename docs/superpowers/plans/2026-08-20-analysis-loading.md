# 解析中ローディング表示とフリーズ解消 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** マスク実行中に GUI が「応答なし」にならないようにし、解析中であることをローディング表示で示す。

**Architecture:** ①GiNZA モデルを `core/ner.py` のモジュールレベルでキャッシュし、Pipeline 再構築のたびの再ロード（数秒〜十数秒）をなくす。②単一ファイル／クリップボードの解析（NER）を既存の `MaskWorker`（QThread）で実行し、GUI スレッドはプレビュー表示とマスク書き出しだけを行う。③解析中は進捗バーの不確定モード＋「解析中…」ラベルで状態を可視化する。

**Tech Stack:** Python 3.11+ / PySide6 / spaCy+GiNZA / pytest / uv

## Global Constraints

- `pyproject.toml` の `spacy>=3.4.4,<3.8.0` と `numpy<2` のピンは変更しない
- レポート・ログ・エラーメッセージ・GUI 表示には検出値そのものを載せない（ファイル名・カテゴリ・件数のみ可）
- GUI は `services/` のみを import する（`core/`・`handlers/` を直接 import しない）
- lint / format ツールは導入しない
- テスト実行は `uv run pytest ...`

仕様: `docs/superpowers/specs/2026-08-20-analysis-loading-design.md`

---

### Task 1: GiNZA モデルのプロセス内キャッシュ

**Files:**
- Modify: `src/privacyprotection/core/ner.py`
- Test: `tests/core/test_ner_cache.py`（新規）

**Interfaces:**
- Consumes: なし
- Produces: `NerDetector.detect(text: str) -> list[Detection]`（既存シグネチャ不変）。モジュール内部に `_load_model() -> spacy.Language` と `_NLP`／`_NLP_LOCK` を追加。外部から見た挙動は「複数インスタンスでもモデルロードは1回」のみ変わる。

- [ ] **Step 1: 失敗するテストを書く**

`tests/core/test_ner_cache.py` を新規作成（実モデル不要。既存 `tests/core/test_ner.py` は実モデルでの検出品質テストなので触らない）:

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/core/test_ner_cache.py -v`
Expected: FAIL（`AttributeError: ... has no attribute '_NLP'` — キャッシュ未実装のため monkeypatch.setattr が失敗する）

- [ ] **Step 3: 実装**

`src/privacyprotection/core/ner.py` の先頭部と `NerDetector` を次のように変更する（`_LABEL_MAP` と `detect` 内の変換ループは既存のまま）:

```python
"""GiNZAによる固有表現抽出。モデルは遅延ロードし、プロセス内で共有する。"""
from __future__ import annotations

import threading

from .models import Detection

# （_LABEL_MAP は既存のまま）

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
```

`__init__` と `_load` メソッドは削除する（呼び出し側は `NerDetector()` を引数なしで構築しており、他に参照はない）。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/core/ -v`
Expected: 全 PASS（ja_ginza 未インストール環境では test_ner.py はスキップされる。それ以外に FAIL がないこと）

- [ ] **Step 5: コミット**

```bash
git add src/privacyprotection/core/ner.py tests/core/test_ner_cache.py
git commit -m "perf: GiNZAモデルをプロセス内でキャッシュし毎回の再ロードをなくす"
```

---

### Task 2: 解析のワーカー化とローディング表示

**Files:**
- Modify: `src/privacyprotection/gui/main_window.py`
- Modify: `docs/04-ui-and-operations.md`（進捗バーの記述 :29-30 と §4.6 の表 :152、経路表 :52-53）

**Interfaces:**
- Consumes: `MaskWorker(fn, *args)`（`gui/worker.py`、変更不要）、`Pipeline.analyze_file(path, fragments)` / `Pipeline.analyze_text(text)`（`services/pipeline.py`、変更不要）
- Produces: GUI 内部のみ。`_run_worker(fn, *args, on_done, busy_message: str)`、`_begin_busy(message: str)` / `_end_busy()`、逐次キュー `self._mask_queue: list[tuple[Path, list[Fragment]]]` と `self._queue_pipeline: Pipeline | None`

GUI 層は既存方針どおり自動テスト対象外（QApplication とスレッド連携が必要）。手動確認手順を Step 4 に置く。

- [ ] **Step 1: ローディング UI（状態ラベル＋busy ヘルパー＋進捗表示）を実装**

`__init__` の進捗バー生成部（`self.progress = QProgressBar()` の直前）に状態ラベルを追加:

```python
        # 解析・処理中の状態表示（検出値は載せない: ファイル名・件数のみ）
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
```

`_set_controls_enabled` の近くに busy ヘルパーを追加:

```python
    def _begin_busy(self, message: str) -> None:
        """ワーカー実行中の表示。進捗総数が不明な間は不確定モード
        （バーが流れるアニメーション）で「実行中」であることだけを示す。
        フォルダ一括処理は progress シグナル受信時（_on_progress）に
        確定モードへ切り替わる。"""
        self.status_label.setText(message)
        self.status_label.setVisible(True)
        self.progress.setRange(0, 0)  # 不確定モード
        self.progress.setVisible(True)
        self._set_controls_enabled(False)

    def _end_busy(self) -> None:
        self.progress.setVisible(False)
        self.progress.setRange(0, 1)  # 不確定モードを解除して次回に備える
        self.status_label.setVisible(False)
        self._set_controls_enabled(True)
```

`_on_progress` を確定モード切り替え＋ラベル更新に変更:

```python
    def _on_progress(self, i, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(i)
        self.status_label.setText(f"処理中: {name} ({i}/{total})")
```

- [ ] **Step 2: `_run_worker` の busy 化とチェーン起動対応、完了・失敗ハンドラの整理**

`_run_worker` を差し替える。`busy_message` を必須の運用にし（呼び出し側は全箇所で渡す）、進捗バー表示と操作無効化は `_begin_busy` に委譲する:

```python
    def _run_worker(self, fn, *args, on_done, busy_message: str):
        # 前回のワーカーがまだ実行中のまま self._worker を差し替えると、
        # 生きているQThreadへの参照を黙って失う（Finding 5）。キュー連鎖では
        # finished_ok 発火時点で run() は完了しているがスレッド終了処理が
        # わずかに残ることがあるため、まず短時間 wait して確定させる。
        # それでも実行中なら本当に処理中なので新規実行を拒否する。
        if self._worker is not None and self._worker.isRunning():
            if not self._worker.wait(100):
                QMessageBox.information(
                    self, "処理中", "前の処理が完了するまでお待ちください")
                return
        self._begin_busy(busy_message)
        self._on_done = on_done
        self._worker = MaskWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()
```

`_on_finished` は **busy 解除を先に行ってから** `_on_done` を呼ぶ形に変える（`_on_done` がキューの次のワーカーを起動して再度 `_begin_busy` するため、従来の finally で後から解除すると次のワーカー実行中に操作が有効化されてしまう）:

```python
    def _on_finished(self, result):
        # 先に busy を解除する: _on_done はプレビュー（モーダル）を開いたり、
        # キューの次のワーカーを起動して再度 _begin_busy したりするため、
        # 従来のように finally で後から解除すると次の実行中に操作が
        # 有効化されてしまう。例外の握りつぶし防止（--windowed ビルドで
        # 無言死しない）のための try/except は維持する。
        self._end_busy()
        try:
            self._on_done(result)
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

    def _on_failed(self, message):
        self._end_busy()
        QMessageBox.critical(self, "エラー", message)
        self._mask_next()  # 単一ファイルキューの途中失敗でも残りを続行する
```

`__init__` にキューの初期値を追加（`self._on_done = None` の近く）:

```python
        self._mask_queue: list[tuple[Path, list]] = []
        self._queue_pipeline: Pipeline | None = None
```

- [ ] **Step 3: 単一ファイル／クリップボードの解析をワーカーへ移す**

`_dispatch_paths` のファイルループを「復元は同期のまま・マスクはキューへ」に変える。フォルダ経路は `busy_message` を渡すだけ:

```python
    def _dispatch_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        action_mode = self.advanced_panel.action_mode()

        if len(paths) == 1 and paths[0].is_dir():
            root = paths[0]
            intent = decide_folder_intent(root, action_mode)
            if self._wants_restore(intent, action_mode, "フォルダ"):
                self._run_worker(pipeline.restore_folder, root,
                                 on_done=self._on_folder_restored,
                                 busy_message="フォルダを復元中…")
            else:
                self._run_worker(pipeline.mask_folder, root,
                                 on_done=self._on_folder_masked,
                                 busy_message="フォルダを解析中…")
            return

        # 断片読み込み・意図判定・復元は従来どおり同期（NERを使わず高速）。
        # 遅い解析（NER）だけをワーカーに逃がすため、マスク対象はキューに
        # 積んで1件ずつ処理する（プレビューがモーダルなので並列にしない）。
        for p in paths:
            try:
                fragments = pipeline.read_fragments(p)
                intent = decide_file_intent(
                    p, [f.text for f in fragments], action_mode)
                if self._wants_restore(intent, action_mode, "ファイル"):
                    self._restore_one(pipeline, p)
                else:
                    self._mask_queue.append((p, fragments))
            except Exception as exc:
                QMessageBox.warning(
                    self, "エラー",
                    f"{p.name}: {type(exc).__name__}: 処理できませんでした")
        if self._mask_queue:
            self._queue_pipeline = pipeline
            self._mask_next()
```

`_mask_one` を「解析はワーカー・確定処理はGUIスレッド」の3メソッドに分割する（既存の `_mask_one` は削除）:

```python
    def _mask_next(self):
        """キューの先頭ファイルの解析をワーカーで開始する。空なら終了。"""
        if not self._mask_queue:
            self._queue_pipeline = None
            return
        p, fragments = self._mask_queue.pop(0)
        self._run_worker(
            self._queue_pipeline.analyze_file, p, fragments,
            on_done=lambda result, p=p: self._on_file_analyzed(p, result),
            busy_message=f"解析中… {p.name}")

    def _on_file_analyzed(self, p: Path, result):
        # 従来 _dispatch_paths のループが担っていた「1件の失敗で残りを
        # 巻き込まない」を、非同期化後はここで担う（メッセージ形式も同じ）。
        try:
            frags, dets = result
            self._finish_mask_one(self._queue_pipeline, p, frags, dets)
        except Exception as exc:
            QMessageBox.warning(
                self, "エラー",
                f"{p.name}: {type(exc).__name__}: 処理できませんでした")
        finally:
            self._mask_next()

    def _finish_mask_one(self, pipeline: Pipeline, p: Path, frags, dets):
        """解析済みファイルのプレビュー確認とマスク書き出し（GUIスレッド）。
        mask_file は検出済みリストを使うためNERを再実行せず高速。"""
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog(frags, dets, parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return
            self._register_manual_entries(dlg.accepted_manual_entries())
        _, report = pipeline.mask_file(p, frags, dets)
        self._show_report_text(self._single_report(report))
```

`_mask_clipboard` を分割し、解析をワーカーで実行する（`analyze_text` 以降の本体はそのまま `_on_clipboard_analyzed` へ移す。空チェックと `_build_pipeline` は同期側に残る）:

```python
    def _mask_clipboard(self):
        cb = QGuiApplication.clipboard()
        text = cb.text()
        if not text:
            QMessageBox.information(self, "クリップボード", "テキストがありません")
            return
        pipeline = self._build_pipeline()
        # 解析（NER）は遅いのでワーカーで実行し、完了後にプレビューへ。
        # 例外は MaskWorker.failed → _on_failed 経由で必ずダイアログになる
        # （従来 _clipboard_action の try/except が担っていた役割）。
        self._run_worker(
            pipeline.analyze_text, text,
            on_done=lambda result: self._on_clipboard_analyzed(
                pipeline, text, result),
            busy_message="解析中… クリップボード")

    def _on_clipboard_analyzed(self, pipeline: Pipeline, text: str, result):
        frag, dets = result
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog([frag], [dets], parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return  # キャンセル時はクリップボードを変更しない
            self._register_manual_entries(dlg.accepted_manual_entries())

        pmap = default_config_path().parent / "clipboard.pmap.csv"
        pmap.parent.mkdir(parents=True, exist_ok=True)
        masked, table, count = pipeline.mask_text(
            text, mapping_path=pmap, detections=dets)
        QGuiApplication.clipboard().setText(masked)
        if count == 0:
            QMessageBox.information(self, "マスク完了",
                                    "検出0件です（検出漏れの可能性があります）")
        elif table.entries:
            QMessageBox.information(
                self, "マスク完了",
                f"{count}件をマスクしてコピーしました。\n対応表: {pmap}")
        else:
            QMessageBox.information(self, "マスク完了",
                                    f"{count}件をマスクしてコピーしました。")
```

既存 `_mask_clipboard` 内の redact モード判定コメント（「検出0件の理由は…」）は `_on_clipboard_analyzed` の該当箇所へそのまま移設する。

- [ ] **Step 4: 全テスト実行と手動確認**

Run: `uv run pytest tests/ -v`
Expected: 全 PASS（GUI変更はテスト対象外だが、import 破壊がないことの確認）

手動確認（`uv run python -m privacyprotection.gui.app`）:
1. テキストファイルをドロップ →「解析中… <ファイル名>」とバーの流れるアニメーションが出て、ウィンドウをドラッグ移動できる（応答なしにならない）
2. 解析完了でプレビューが開き、確定するとレポートが出る
3. 同じファイルをもう一度ドロップ → 2回目はほぼ即時にプレビューが開く（モデルキャッシュの効果）
4. 複数ファイルを同時ドロップ → 1件ずつ「解析中…」→プレビューの順で処理される
5. クリップボード処理 →「解析中… クリップボード」表示後にプレビューが開く
6. フォルダをドロップ → 最初は不確定バー、progress 受信後「処理中: <ファイル名> (i/total)」と確定バーに切り替わる
7. 実行中に操作（ドロップ・ボタン・Ctrl+V）が無効化され、完了後に戻る

- [ ] **Step 5: docs/04 を現状に合わせて更新**

`docs/04-ui-and-operations.md` の3箇所:

1. `:29-30` の進捗バー記述を差し替え:
```markdown
- 状態表示: 処理中は「解析中…」等のラベルと進捗バーを表示する。単一ファイル・クリップボードの
  解析中は不確定モード（流れるバー）、フォルダ一括処理中はファイル名と件数付きの確定バー。
  処理中は操作を無効化し、前の処理の完了前に次を始めさせない
```

2. `:52-53` のファイル経路2行に「解析はワーカースレッドで実行し、」を先頭に追記（例:「ファイルごとに解析をワーカースレッドで実行→プレビュー（§4.2）→ 確定したものだけマスクし、…」。複数ファイルは1件ずつ順に処理する旨も追記）

3. `:152` の§4.6の表の行を更新:
```markdown
| 時間のかかる処理（フォルダ一括、単一ファイル・クリップボードの解析） | ワーカースレッドで処理し、状態ラベルと進捗バーを表示する。処理中は操作を無効化する（→ 中断手段は[05 E-3](05-known-issues-and-roadmap.md)） |
```

- [ ] **Step 6: コミット**

```bash
git add src/privacyprotection/gui/main_window.py docs/04-ui-and-operations.md
git commit -m "feat: 解析をワーカースレッド化しローディング表示を追加（応答なし解消）"
```
