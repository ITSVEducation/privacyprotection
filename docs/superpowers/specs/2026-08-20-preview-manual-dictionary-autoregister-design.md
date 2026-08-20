# プレビューの手動追加検出をカスタム辞書へ自動登録する — 設計

## 目的

プレビュー画面でドラッグにより手動追加した検出（`source == "manual"`）は、その場限りで
失われる。同じ語句を次のファイルでもマスクしたければ、設定画面のカスタム辞書タブへ
手で登録し直す必要がある。これを、「この内容でマスク実行」（OK）を押した時点で有効
（チェックあり）な手動追加検出を自動的にカスタム辞書へ登録することで解消する。

## 仕様

- **登録対象**: OK 押下時点で `enabled == True` かつ `source == "manual"` の検出すべて。
  カテゴリは問わない（右クリックで「人名」等へ変更した手動追加も、そのカテゴリで登録する）。
- **登録内容**: `{Detection.text: Detection.category}` のペア。同じ語句の手動検出が複数
  あれば最初に現れたものが勝つ。
- **重複時**: 既存の辞書に同じ語句があれば上書きしない（既存優先）。意図せぬ種別の
  塗り替えを防ぐ。
- **通知**: しない（サイレント）。登録内容は設定画面のカスタム辞書タブでいつでも
  確認・削除できる。
- **適用フロー**: ファイルのマスク（`MainWindow._mask_one`）とクリップボードのマスク
  （`MainWindow._mask_clipboard`）の両方。プレビューをスキップする設定
  （「確認なしで即変換」）のときは手動追加自体が存在しないため対象外。
- **効果のタイミング**: 登録は次回以降の解析（`DictionaryDetector`）から効く。今回の
  実行分は既に手動検出としてマスクされるため影響しない。
- **キャンセル時**: ダイアログを Cancel で閉じた場合は何も登録しない。

## 実装アプローチ

責務分担は「ダイアログは編集結果を提供するだけ、永続化は呼び出し元（MainWindow）」
とする。`PreviewDialog` は現状 fragments/detections しか受け取らない設計であり、
config への依存を持ち込まない。

### PreviewDialog（gui/preview_dialog.py）

`accepted_manual_entries() -> dict[str, str]` を追加する。全断片の検出を走査し、
`enabled` かつ `source == "manual"` のものから `{text: category}` を組み立てて返す。
同じ語句が複数あれば最初のものを採用する（`setdefault` 相当）。

### MainWindow（gui/main_window.py）

`_mask_one` と `_mask_clipboard` の Accepted 後に共通ヘルパーを呼ぶ:

```
def _register_manual_entries(self, entries: dict[str, str]) -> None
```

- `self._config.custom_dictionary` に無いキーだけを追加する。
- 1件でも追加したら `save_config(self._config)` で即保存する（closeEvent 任せに
  しない。アプリが異常終了しても登録が残るように）。
- `save_config` の失敗はマスク処理自体を止めない（既存の設定保存と同じ扱い。
  例外はスロット全体の try/except が拾う）。

## エラー処理

- 登録処理はマスク実行の成否に影響しない。辞書登録に失敗してもマスクは続行する。
- 検出値そのものをログ・ダイアログに出さない不変条件は維持する（登録は無言で行い、
  件数すら表示しないためそもそも露出しない）。

## テスト

`tests/gui/` の PreviewDialog テスト（無ければ新設）に以下を追加する:

- `accepted_manual_entries()` が有効な manual 検出のみを返す。
- 無効化（チェックを外した）manual 検出は含まれない。
- カテゴリ変更後の manual 検出は変更後のカテゴリで返る。
- `pattern` / `ner` / `dictionary` 由来の検出は enabled でも含まれない。
- MainWindow のマージロジック: 既存語句を上書きせず、新規語句だけが
  `custom_dictionary` に加わり保存されること（`_register_manual_entries` を
  直接テストする）。

## ドキュメント反映

`docs/` のプレビュー画面・カスタム辞書の該当節に「マスク実行時、有効な手動追加検出は
そのカテゴリでカスタム辞書へ自動登録される（既存語句は上書きしない）」を追記する。
