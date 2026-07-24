# プレビュー編集の直接操作化とクリップボード・プレビュー — 設計書

- 日付: 2026-07-24
- 対象: `gui/main_window.py`, `gui/preview_dialog.py`, `services/pipeline.py`
- 関連仕様: `docs/04-ui-and-operations.md`（§6.1 メイン画面 / §6.2 プレビュー画面）

## 1. 背景と目的

現状の GUI には 2 つの操作上のギャップがある。

1. **クリップボードのマスクにプレビューが無い。** ファイルのマスクは
   `PreviewDialog` で検出結果を目視・編集してから確定できるが
   （`main_window.py` `_mask_paths`）、クリップボードのマスク
   （`_mask_clipboard`）は読み取り→即マスク→即コピーで、確認の機会が無い。
   AI へ貼り付ける直前の最終ゲートであるクリップボード経路こそ、検出漏れ／
   過検出を目視できることが望ましい。

2. **プレビュー左ペインの編集が間接的。** 左の原文ビューは読み取り専用で、
   マスクの追加は「テキスト選択 → 右クリック → カテゴリを選ぶ」、削除
   （無効化）は「右のリストでチェックを外す」と、視線と操作が左右に分かれる。
   選択した箇所をその場で足す／消す直接操作が求められている。

本設計はこの 2 点を、既存のアーキテクチャ制約（GUI は `services/` にのみ
依存し `core/`・`handlers/` を直接 import しない）を保ったまま解消する。

## 2. スコープ

**やること**
- クリップボードのマスク時に、ファイルと同じ `PreviewDialog` を表示する
  （「確認なしで即変換」設定に従う）。
- プレビュー左ペインで、ドラッグ選択→即カスタム追加、ハイライトのクリック→
  無効化、を可能にする。
- カテゴリ変更手段を右リスト側へ移す（右クリックメニュー）。
- 上記を支える `services/pipeline.py` の公開 API を追加する。

**やらないこと（YAGNI）**
- クリップボードの**復元**フロー（`_restore_clipboard`）は変更しない。
- マスク方式（可逆/不可逆）や検出ロジック自体は変更しない。
- 左ペインでのテキスト直接編集（打ち込み）は行わない（読み取り専用のまま）。
- 検出値をレポート／エラーに出さないハード制約は現状どおり維持する。

## 3. Part 1 — クリップボードのプレビュー

### 3.1 services/pipeline.py の追加・変更

**追加: `analyze_text(text) -> tuple[Fragment, list[Detection]]`**

生テキストを 1 つの `Fragment(location="clipboard")` として包み、検出器を
かけて返す。`analyze_file` の生テキスト版であり、GUI が `core/` の検出器に
触れずに済むようにするための薄い公開 API（既存の `analyze_file` と同じ役割）。

```python
def analyze_text(self, text: str) -> tuple[Fragment, list[Detection]]:
    fragment = Fragment(text=text, location="clipboard")
    return fragment, self._detector.detect(text)
```

**変更: `mask_text` に `detections` 引数を追加**

現状 `mask_text` は内部で毎回 `self._detector.detect(text)` を呼ぶ。プレビューで
ユーザーが編集した検出リストをそのままマスクできるよう、任意引数
`detections: list[Detection] | None = None` を追加する。`None` のときは従来どおり
内部検出（後方互換）。指定時はそのリストを使う。件数カウント・対応表書き出しの
既存挙動（redact モードでは `table.entries` が常に空になる点を含む）は不変。

```python
def mask_text(self, text, masker=None, mapping_path=None, detections=None):
    if detections is None:
        detections = self._detector.detect(text)
    active_count = sum(1 for d in detections if d.enabled)
    m = masker or Masker(mode=self._mode)
    m.scan_existing_tokens([text])
    masked_texts, table = m.mask_fragments([text], [detections])
    if self._mode == "token" and mapping_path is not None and table.entries:
        _atomic_write(lambda p: write_mapping(table, p), mapping_path)
    return masked_texts[0], table, active_count
```

### 3.2 main_window.py `_mask_clipboard` の変更

```
1. text = clipboard.text(); 空なら「テキストがありません」で return（現状どおり）
2. pipeline = self._build_pipeline()
3. frag, dets = pipeline.analyze_text(text)
4. if not self.skip_preview.isChecked():
       dlg = PreviewDialog([frag], [dets], parent=self)
       if dlg.exec() != PreviewDialog.Accepted:
           return            # キャンセル時は何もコピーしない
       # dlg が [dets] を in-place 編集済み。編集後リストは dets（=[...][0]）
5. masked, table, count = pipeline.mask_text(
       text, mapping_path=pmap, detections=dets)
6. clipboard.setText(masked)
7. 件数に応じた結果ダイアログ（現状の 0件／件数＋対応表／件数のみ の分岐を流用）
```

`PreviewDialog` に渡す `detections` は `[dets]`（1 断片ぶんのリストのリスト）。
ダイアログは `self.detections[0]` を in-place 編集するため、確定後は同じ
`dets` オブジェクトが編集結果を保持している。これを `mask_text(detections=dets)`
へ渡す。

「確認なしで即変換」ON のときは 3〜4 を飛ばし、`analyze_text` の結果を
そのままマスクする（＝実質、内部検出を 1 回で済ませつつ従来と同じ即マスク）。

例外は既存の `_clipboard_action` の try/except（`--windowed` ビルドでの無言死を
防ぐためスロット全体を包む既存対策）でこれまでどおり捕捉される。

## 4. Part 2 — プレビュー左ペインの直接操作

### 4.1 操作仕様

| 操作 | 結果 |
|---|---|
| 左ペインでドラッグ選択して離す | 選択範囲を **カスタム**（`CUSTOM`, source="manual"）の検出として即追加 |
| 単語をダブルクリック | 同上（選択が生じるため即カスタム追加） |
| ハイライト部分を単純クリック（選択なし） | その検出を**無効化**（右リストのチェックを外すのと同義。再有効化は右リストで） |
| ハイライトでない箇所をクリック | 何もしない |
| 右リストの項目を右クリック | **カテゴリ変更**メニュー（カスタム→人名 等） |

- 無効化した検出はハイライトが消えるため左ペインからは再クリックで戻せない。
  復活はこれまでどおり右リストのチェックで行う（一貫性のため既存の
  チェックボックスを唯一の再有効化手段とする）。
- 左ペインの旧「右クリック→○○として追加」コンテキストメニューは**廃止**する。
  追加はドラッグに一本化され、カテゴリ選択は右リストへ移るため、これを残すと
  ドラッグ確定後に同じ選択を右クリックして二重追加してしまう不整合が生じる。

### 4.2 実装方針

**左ペインを `QTextEdit` のサブクラスに差し替える。**

```python
class _InteractiveTextView(QTextEdit):
    """読み取り専用のまま、離した時のマウス操作を親へ通知する。
    選択あり → 範囲を通知（＝カスタム追加）／選択なし → クリック位置を通知
    （＝その位置の検出を無効化）。"""
    def __init__(self, on_select, on_click, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._on_select = on_select
        self._on_click = on_click

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() != Qt.LeftButton:
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._on_select(cursor.selectionStart(), cursor.selectionEnd())
        else:
            self._on_click(cursor.position())
```

**`_add_manual` を (start, end, category) 受け取りへリファクタ。**
現状は `cursor` を受け取り内部で `selectionStart/End` を読む。左ペインの
サブクラスから渡す連結オフセット基準の (start, end) をそのまま受け取れるよう
引数化する。断片境界をまたぐ選択を中止する既存ガード（マスク漏れ防止のため
黙って切り詰めない）はそのまま維持する。

**カスタム追加時の重複情報ダイアログは出さない。**
ドラッグ即追加を軽快にするため、既存 `_add_manual` の「既存検出と重複」
情報ダイアログ（`QMessageBox.information`）は表示しない。重複は従来どおり
`core/masker.py` の `resolve_overlaps` が正しく解決し、ハイライト表示で結果が
見えるため、ドラッグごとにダイアログが出るのを避ける。**断片境界をまたぐ
場合の警告ダイアログは残す**（マスク漏れに直結するため）。

**クリック無効化 `_disable_at(pos)`。**
連結テキスト上の位置 `pos` を既存の `self._offsets` で断片 index とオフセットに
変換し、その位置を含む**有効な**検出のうち 1 件（複数重なる場合は最後に
追加された＝最前面のもの）を `enabled = False` にして `_refresh()`。無効化された
検出は右リストで自動的にチェックが外れる（`_refresh` がチェック状態を
`d.enabled` から再構築するため）。

**右リストのカテゴリ変更メニュー。**
`list_view` に `CustomContextMenu` を設定し、右クリックで
`LABEL_TO_CATEGORY`（ラベル重複を排除した代表カテゴリ）のメニューを出す。
選択で当該 `Detection.category` を書き換え、`_refresh()` で項目ラベルと
ハイライト色を更新（チェック状態＝ `enabled` は維持）。

### 4.3 不変条件

- `Detection.text == original_text[start:end]` の不変条件（CLAUDE.md）を
  カスタム追加でも維持する。`_add_manual` は断片内オフセットで
  `self.fragments[fi].text[frag_start:frag_end]` から `text` を作るため保たれる。
- 検出値をダイアログ／レポートへ出さない制約を維持する（境界警告は範囲の
  説明のみで値を含めない、現状どおり）。

## 5. テスト

GUI 層は本プロジェクトの方針どおり自動ユニットテストの対象外（手動確認）。
ただし `services/pipeline.py` の追加分はユニットテストする。

- `analyze_text`: 生テキストから `Fragment(location="clipboard")` と検出リストを
  返すこと。既知の PII を含む文字列で `detections` が非空になること。
- `mask_text(detections=...)`: 明示した検出リストでマスクされること
  （内部再検出されないこと）。空の／無効化した検出を渡すと 0 件になること。
- `mask_text(detections=None)`: 従来どおり内部検出でマスクされること（後方互換）。

手動確認手順（uv 例）:

```bash
uv run python -m privacyprotection.gui.app
```

1. クリップボードに PII を含むテキストを置き、「確認なしで即変換」OFF で
   「クリップボードをマスクしてコピー」→ プレビューが出ること。キャンセルで
   クリップボードが変わらないこと。ON で従来どおり即マスクされること。
2. プレビュー左ペインでドラッグ → 選択範囲がカスタムでハイライトされ右リストに
   追加されること。ハイライトをクリック → 無効化されハイライトが消え右リストの
   チェックが外れること。右リスト項目を右クリック → カテゴリ変更が反映される
   こと。断片境界をまたぐドラッグは警告で中止されること。

## 6. リスク・留意点

- **クリックと選択解除の判別**: QTextEdit では単純クリックで選択が消え
  カーソルだけ移動する。離した時点で `hasSelection()` を見れば
  ドラッグ（選択あり）とクリック（選択なし）を確実に区別できる。
- **重複ハイライトのクリック対象**: 複数の有効検出が重なる位置をクリックした
  場合は最後に追加された 1 件を無効化する。段階的に消せるため実害は無い。
- **spacy/ginza のバージョンピン**: NER を含む検出経路に触れるため、動作確認は
  ピン維持の環境（既存 `.venv` 等）で `doc.ents` が出ることを前提とする
  （CLAUDE.md の gotcha）。
