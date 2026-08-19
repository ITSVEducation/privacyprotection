# 02. アーキテクチャ

> 関連文書: [README（目次・用語集）](README.md) / [01 概要・要件](01-overview.md) /
> [03 マスク仕様](03-masking-spec.md) / [04 画面・運用](04-ui-and-operations.md) /
> [05 現状の課題・将来拡張](05-known-issues-and-roadmap.md)

本アプリの構造・処理の流れ・実装を保守する上での約束事をまとめる。検出/マスク/復元の
**詳細な仕様**は[03 マスク仕様](03-masking-spec.md)にある。

---

## 2.1 技術選定

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

> **依存バージョン制約**: `spacy>=3.4.4,<3.8.0` と `numpy<2` は**どちらの上限も必須**で、片方でも
> 外すとGiNZAのNERが沈黙し、マスクが何も検出しなくなる。`click` は spacy が依存宣言なしに実行時
> importするためピンしている。理由の詳細は `pyproject.toml` のコメントと
> [`AGENTS.md`](../AGENTS.md) にある。NLP依存を触る場合は、変更後に `spacy.load("ja_ginza")` が
> 実際に固有表現（`doc.ents`）を返すことを必ず再確認すること。

---

## 2.2 レイヤ構成

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
│  フォルダ/生テキスト単位のマスク・復元を統括。   │
│  レポート生成                               │
├─────────────────────────────────────────┤
│  コア層 (純Python・GUI非依存)               │
│  ・Detector: 検出エンジン                   │
│    - PatternDetector（正規表現）            │
│    - DictionaryDetector（カスタム辞書）      │
│    - NerDetector（GiNZA）                  │
│  ・Masker: 置換エンジン（可逆／不可逆）       │
│  ・Restorer: 復元エンジン（対応表→逆置換）    │
│  ・spans: 区間ユーティリティ（純粋関数）      │
├─────────────────────────────────────────┤
│  ファイルI/O層 (Handler共通インターフェース)   │
│  TextHandler / XlsxHandler / DocxHandler / │
│  PptxHandler / FolderWalker（再帰一括処理）  │
└─────────────────────────────────────────┘
```

GUIが検出器を組み立てたり生テキストを検出したりする必要がある場面（クリップボード経路）では、
GUIが `core/` を直接importするのではなく、サービス層が `Pipeline.from_config()` /
`Pipeline.analyze_text()` / `Pipeline.mask_text()` を公開APIとして提供して境界を保つ。同じ理由で、
GUIが意図の自動判定（[04 §4.1](04-ui-and-operations.md)「意図の自動判定」）のためにファイルの
断片テキストを必要とする場面では、`handlers/` を直接importせずに `Pipeline.read_fragments()` を
使う（検出は行わず断片テキストを読むだけの薄いAPI）。

## 2.3 コア層の主要インターフェース

```python
@dataclass
class Detection:
    text: str          # 検出された文字列（例: "山田太郎"）
    category: str      # 種別（PERSON / ORG / LOC / PHONE / EMAIL / ADDRESS / CUSTOM 等）
    start: int         # テキスト断片内の開始位置
    end: int           # 終了位置
    source: str        # 検出元（pattern / dictionary / ner / manual）
    enabled: bool      # プレビューでの採否（既定 True）

class Detector:
    def detect(self, text: str) -> list[Detection]: ...

class Masker:                        # mode は "token" / "redact"
    def scan_existing_tokens(self, fragments: list[str]) -> int: ...
    def mask_fragments(self, fragments: list[str],
                       detections: list[list[Detection]]
                       ) -> tuple[list[str], MappingTable]: ...

class Restorer:                      # 対応表を受け取って構築する
    def restore(self, text: str) -> RestoreResult: ...
    # RestoreResult = 復元後テキスト + 未知トークン一覧（警告表示用）
```

`core/spans.py` は検出器・マスカーに依存しない文字列区間の純粋関数を持つ。`remaining_spans()` /
`resolve_overlaps()` が重複解決（[03 §3.1](03-masking-spec.md)）の唯一の実装で、`patterns.py` と
`detector.py` の両方から使われる。`find_occurrences()` はある値の完全一致・非重複の全出現を返し、
プレビューの「同じ語をまとめて扱う」（[04 §4.2](04-ui-and-operations.md)）が使う。

> `Detection.text` は常に `original_text[start:end]` と完全一致していなければならない。範囲計算を
> 触るときはテストで明示的に検証すること。根拠と検証手順は `.claude/rules/core-invariants.md` にある。

## 2.4 ファイルI/O層の共通インターフェース

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
各形式が具体的にどこを対象とするかは[03 §3.6 各形式のマスク対象範囲](03-masking-spec.md)を参照。

---

## 2.5 データフロー（マスク時）

1. ユーザーがファイル／フォルダをドロップ、またはクリップボード読込
2. Handler がテキスト断片（Fragment）を抽出（クリップボードは全体を1断片として扱う）
3. Detector が検出リストを作成（重複解決済み）
4. プレビュー画面で原文ハイライト表示 → ユーザーが除外／手動追加（「確認なし」時はスキップ）
5. Masker が置換実行
6. 出力: `<元名>_masked.<拡張子>` ＋（可逆時）`.pmap.csv`。元ファイルは変更しない
7. **処理後レポートを表示**（→ [04 §4.4](04-ui-and-operations.md)）

## 2.6 データフロー（復元時）

1. 復元対象を指定: マスク済ファイル、AI回答を貼り付けたテキスト／クリップボード、またはフォルダ
   （`*_masked` 命名のファイルを一括復元。境界条件は2.8）
2. 対応する `.pmap.csv` を指定（同フォルダにあれば自動検出）。フォルダ一括復元では、ファイルごとの
   サイドカー（`<マスク済ファイル名>.pmap.csv`）を共有の `_folder.pmap.csv` より優先する
   ——単体マスクは専用の `Masker` を使うためトークンの採番がファイルごとに1から独立にやり直され、
   同じトークンでも共有表とサイドカーとでは指す実際の値が異なり得るため、共有表を優先すると
   警告なしに別人の値を復元してしまう
3. Restorer がトークンを完全一致で逆置換。対応表にないトークン・置換されず残ったトークンは警告一覧に表示

## 2.7 出力の原子性（クラッシュ・中断対策）

- マスク済ファイル・対応表は**一時ファイルに書き込み完了後、リネームで確定**する（部分書き込みされた出力を残さない）
- 対応表はマスク済ファイルより**先に**確定する（マスク済ファイルだけ存在して復元不能になる状態を作らない）
- 中断時は一時ファイルを削除する。処理済みの完成ペア（マスク済＋対応表）は残す
- フォルダ一括処理では、各ファイルのマスク済出力を書く前に、蓄積済みの共有対応表を毎回書き直す
  （途中でクラッシュしても、出力済みファイルのトークンが必ず対応表に記録されている状態を保つ）

## 2.8 フォルダ再帰処理の境界条件

- シンボリックリンク・ジャンクション（NTFS junction）は**辿らない**（無限ループ防止）
- 隠しファイル・システムファイル・`~$` で始まるOfficeロックファイルはスキップ（Windowsのファイル属性で判定）
- 未対応拡張子はスキップし、レポートに件数と一覧を表示（「処理されなかったファイル」を明示することで、未処理ファイルをAIに渡してしまう事故を防ぐ）
- マスク時（`walk()`）: 出力ファイル（`_masked` 付き・`.pmap.csv`）自体は再処理の対象にしない
- 復元時（`walk_masked()`）: シンボリックリンク／ジャンクション・隠し/システムファイル・`~$`ロック
  ファイルの境界条件は `walk()` と共有しつつ、選別は逆にする——`_masked` 付きのファイルだけを
  一括復元の対象として集める（マスク時に除外される対象こそが、復元時に集めたい対象そのものであるため）
