# AGENTS.md

## このプロジェクトについて

AI サービスに文章を貼り付ける前に、個人情報・機密情報をマスクし、あとから復元できる完全ローカル
動作の Windows デスクトップアプリ（PySide6）。実装は一通り完了している。

`src/` レイアウトで、パッケージは `src/privacyprotection/` にある。
仕様は `docs/` にある（`docs/README.md` から読む）。ドキュメントの運用ルールは末尾の「ドキュメント」節を参照。

## コマンド

依存関係は **uv** で管理する（`uv.lock` が正）。Python 3.11 以上。`.venv/` は uv が管理しているので
pip で直接インストールしない。

```bash
uv sync --extra dev                    # uv.lock から venv を作成／更新
uv run pytest tests/ -v                # テスト全実行
uv run pytest tests/core/test_masker.py::test_name -v   # テスト単体実行
uv run python -m privacyprotection.gui.app              # GUI 起動
powershell -ExecutionPolicy Bypass -File scripts/build.ps1   # PyInstaller ビルド
```

lint / format ツールは導入していない。指示なく新たに導入しない。

## 重要：動かしてはいけない依存バージョン制約

`pyproject.toml` の `spacy>=3.4.4,<3.8.0` と `numpy<2` は、**どちらの上限も必須**。どちらか一方でも
外すと GiNZA の NER が壊れ、マスクが何も検出しなくなる。しかもその症状は「ピンが誤っている」ではなく
「モデルが入っていない」ように見える。理由は `pyproject.toml` のコメントにある。PreToolUse フック
（`scripts/hooks/guard-dependency-pins.ps1`）がこの制約を外す編集をブロックする。意図的に上限を
引き上げる場合は、先に `spacy.load("ja_ginza")` が実際に `doc.ents` を返すことを確認する。
`click` は spacy が依存宣言なしに実行時 import するためピンしている。

## アーキテクチャ

GUI → services → core + handlers の 3 層で、依存は一方向。core と handlers は互いに import しない。
インターフェース・データフロー・出力の原子性・フォルダ走査の境界条件は `docs/02-architecture.md` が正。
ソースから再導出せず、そちらを読むこと。

| レイヤ | 役割 |
|---|---|
| `gui/` | PySide6。`services/` のみを呼ぶ |
| `services/` | handlers と core を束ね、ファイル単位／フォルダ単位のマスク・復元とレポート生成を行う |
| `core/` | `PatternDetector` / `DictionaryDetector` / `NerDetector`（`Detector` が統合）、`Masker`、`Restorer`。文字列に対する純粋な処理で、ファイル I/O・GUI・ネットワークを持たない |
| `handlers/` | フォーマットごとの `FileHandler`。走査対象のテキスト範囲だけを書き換え、書式や数式には触れない |

### プライバシー不変条件 — 決して退行させない

根拠と検証手順は `.claude/rules/core-invariants.md`（`core/` や `services/` を開くと読み込まれる）。
これらのディレクトリを編集すると PostToolUse フックが対応するテストを実行する。

- 重複した検出は **破棄せず、重ならない範囲にトリムする**。
- `Detection.text` は常に `original_text[detection.start:detection.end]` と一致する。
- **バッチごとに `Masker` は 1 つ**。同じ値はフォルダ全体で同じトークンに対応する。
- `Restorer` はトークンを **完全一致でのみ** 照合する。曖昧一致や自動補正はしない。
- 対応表 CSV（`.pmap.csv`）は **意図的に平文**で、読み込み時に重複トークンを拒否する。
- レポート・ログ・エラーメッセージには **カテゴリと件数のみ**を載せ、検出した値そのものは載せない。

## ドキュメント

### docs/ — 確定した設計書

現行システムの設計書（日本語、AI が読む前提）。常にコードの現状と一致させる。

- 日付や経緯は書かない（git 履歴が持つ）
- 現在形で書く。「〜に変更した」ではなく「〜である」
- 関数一覧や処理の逐次説明は書かない（コードとの二重管理になる）
- 実装を変更する際は、該当する docs/ を同じコミット群で更新する

### docs/superpowers/ — 作業用ドキュメント

進行中の設計提案（`specs/`）・実装計画（`plans/`）・レビュー（`specs/*-review.md`）。日付プレフィックス付き。

作業が完了したら、設計の結論を docs/ 直下に蒸留して反映し、元ファイルは削除する。実装手順・チェック
ボックス・移行前の状況説明は反映しない（完了した時点で不要になるため）。

docs/ と docs/superpowers/ が矛盾する場合、docs/ とコードが正。
