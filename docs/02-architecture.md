# 02. アーキテクチャ

> 関連文書: [README（目次・用語集）](README.md) / [01 概要・要件](01-overview.md) /
> [03 マスク仕様](03-masking-spec.md) / [04 画面・運用](04-ui-and-operations.md) /
> [05 現状の課題・将来拡張](05-known-issues-and-roadmap.md)

本アプリの構造・処理の流れ・実装を保守する上での約束事をまとめる。検出/マスク/復元の
**詳細な仕様**は[03 マスク仕様](03-masking-spec.md)にある。

---

## 3. 技術選定

| 項目 | 選定 | 理由 |
|------|------|------|
| 言語 | Python 3.11+ | 日本語NERエコシステムが最も充実 |
| GUI | PySide6 (Qt) | ドラッグ&ドロップ、リッチなハイライト表示、実績 |
| NER | GiNZA (spaCy + ja_ginza) | 完全オフライン動作の日本語固有表現抽出 |
| xlsx | openpyxl | セル・コメント・ヘッダーフッターにアクセス可能 |
| docx | python-docx | 段落・表・ヘッダーフッターの書式保持置換 |
| pptx | python-pptx | スライド・ノートのテキスト置換 |
| パッケージング | PyInstaller | Windows単体配布 |
| テスト | pytest | コア層のユニットテスト |

> **重要な依存バージョン制約**: `spacy` は `>=3.4.4,<3.8.0` に固定する。spacy 3.8以降は
> ja_ginza の `compound_splitter` パイプラインを壊し、ja_ginza 側に修正版が無い。NLP依存を
> 触る場合は、変更後に `spacy.load("ja_ginza")` が実際に固有表現（`doc.ents`）を返すことを
> 必ず再確認すること。

---

## 4. アーキテクチャ

3層構成とし、**コア層はGUIに一切依存しない**。依存の向きは GUI → サービス → コア＋Handler の一方向で、
コア層とHandler層は互いを直接importしない。この分離により、コア層はGUIなしで完全にユニットテストできる。

```
┌─────────────────────────────────────────┐
│  GUI層 (PySide6)                          │
│  メイン画面 / プレビュー画面 / 設定画面 /    │
│  処理後レポート画面                         │
│  ※ services層のみ呼び出す。core/handlersを直接importしない │
├─────────────────────────────────────────┤
│  サービス層 (services/)                     │
│  Pipeline: Handler＋コアを束ね、ファイル/       │
│  フォルダ単位のマスク・復元を統括。レポート生成    │
├─────────────────────────────────────────┤
│  コア層 (純Python・GUI非依存)               │
│  ・Detector: 検出エンジン                   │
│    - PatternDetector（正規表現）            │
│    - DictionaryDetector（カスタム辞書）      │
│    - NerDetector（GiNZA）                  │
│  ・Masker: 置換エンジン（可逆／不可逆）       │
│  ・Restorer: 復元エンジン（対応表→逆置換）    │
├─────────────────────────────────────────┤
│  ファイルI/O層 (Handler共通インターフェース)   │
│  TextHandler / XlsxHandler / DocxHandler / │
│  PptxHandler / ClipboardHandler /          │
│  FolderWalker（再帰一括処理）               │
└─────────────────────────────────────────┘
```

### 4.1 コア層の主要インターフェース

```python
@dataclass
class Detection:
    text: str          # 検出された文字列（例: "山田太郎"）
    category: str      # 種別（PERSON / ORG / LOC / PHONE / EMAIL / ADDRESS / CUSTOM 等）
    start: int         # テキスト断片内の開始位置
    end: int           # 終了位置
    source: str        # 検出元（pattern / dictionary / ner）
    enabled: bool      # プレビューでの採否（既定 True）

class Detector:
    def detect(self, text: str) -> list[Detection]: ...

class Masker:
    def mask(self, fragments: list[str], detections: list[list[Detection]],
             mode: Literal["token", "redact"]) -> tuple[list[str], MappingTable]: ...

class Restorer:
    def restore(self, text: str, mapping: MappingTable) -> RestoreResult: ...
    # RestoreResult = 復元後テキスト + 未知トークン一覧（警告表示用）
```

> `Detection.text` は常に `original_text[start:end]` と完全一致していなければならない。この不変条件は
> 実際にバグ（範囲トリム後にスライスと `.group()` が食い違う）を生んでおり、範囲計算を触るときは
> テストで明示的に検証すること。

### 4.5 ファイルI/O層の共通インターフェース

```python
class FileHandler(ABC):
    extensions: ClassVar[list[str]]
    def read_fragments(self, path: Path) -> list[Fragment]: ...
    def write_fragments(self, src: Path, dst: Path,
                        masked: list[Fragment]) -> None: ...
    # Fragment = ファイル内の位置情報（セル番地・段落番号等）付きテキスト断片。
    # write は元ファイルをコピーした上でテキスト部分のみ差し替え、書式を保持する
```

**新しい拡張子への対応は Handler を1クラス追加するだけで完結させる**（`registry.py` に登録するだけで
サービス層から自動的に使われる）。各Handlerは「PIIを探す価値のある断片だけ」を抽出し、書き戻し時は
その断片のテキストだけを差し替え、書式・数式・無関係なセルには一切触れない。
各形式が具体的にどこを対象とするかは[03 マスク仕様「各形式のマスク対象範囲」](03-masking-spec.md)を参照。

---

## 5. データフロー

### 5.1 マスク時

1. ユーザーがファイル／フォルダをドロップ、またはクリップボード読込
2. Handler がテキスト断片（Fragment）を抽出
3. Detector が検出リストを作成（重複解決済み）
4. プレビュー画面で原文ハイライト表示 → ユーザーが除外／手動追加（「確認なし」時はスキップ）
5. Masker が置換実行
6. 出力: `<元名>_masked.<拡張子>` ＋（可逆時）`.pmap.csv`。元ファイルは変更しない
7. **処理後レポートを表示**（→ [04](04-ui-and-operations.md)）

### 5.2 復元時

1. 復元対象を指定: マスク済ファイル、または AI回答を貼り付けたテキスト／クリップボード
2. 対応する `.pmap.csv` を指定（同フォルダにあれば自動検出）
3. Restorer がトークンを完全一致で逆置換。対応表にないトークン・置換されず残ったトークンは警告一覧に表示

### 5.3 出力の原子性（クラッシュ・キャンセル対策）

- マスク済ファイル・対応表は**一時ファイルに書き込み完了後、リネームで確定**する（部分書き込みされた出力を残さない）
- 対応表はマスク済ファイルより**先に**確定する（マスク済ファイルだけ存在して復元不能になる状態を作らない）
- キャンセル・クラッシュ時は一時ファイルを削除。処理済みの完成ペア（マスク済＋対応表）は残す
- フォルダ一括のキャンセル時は、完了済みファイル一覧をレポートに表示
- フォルダ一括処理では、各ファイルのマスク済出力を書く前に、蓄積済みの共有対応表を毎回書き直す
  （途中でクラッシュしても、出力済みファイルのトークンが必ず対応表に記録されている状態を保つ）

### 5.4 フォルダ再帰処理の境界条件

- シンボリックリンク・ジャンクション（NTFS junction）は**辿らない**（無限ループ防止）
- 隠しファイル・システムファイル・`~$` で始まるOfficeロックファイルはスキップ（Windowsのファイル属性で判定）
- 未対応拡張子はスキップし、レポートに件数と一覧を表示（「処理されなかったファイル」を明示することで、未処理ファイルをAIに渡してしまう事故を防ぐ）
- 出力ファイル（`_masked` 付き・`.pmap.csv`）自体は再処理の対象にしない
