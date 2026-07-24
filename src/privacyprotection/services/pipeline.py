"""マスク・復元のオーケストレーション（設計書 5章）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from ..config import AppConfig
from ..core.detector import Detector
from ..core.dictionary import DictionaryDetector
from ..core.mapping_io import read_mapping, write_mapping
from ..core.masker import Masker
from ..core.models import Detection, Fragment, MappingTable, RestoreResult
from ..core.ner import NerDetector
from ..core.patterns import PatternDetector
from ..core.restorer import Restorer
from ..handlers.folder_walker import MASKED_STEM_RE, MASKED_SUFFIX, walk
from ..handlers.registry import get_handler
from .report import BatchReport, FileReport

# MASKED_SUFFIX/MASKED_STEM_RE は handlers/folder_walker.py で定義されている
# （最終レビュー Finding 8）。folder_walker._is_own_output() がフォルダ一括
# 処理時に自アプリの出力を見分けるのに使うのと、ここ(mask_file/restore_file)
# が実際に出力名を組み立て/復元するのとで、同じ「自アプリの命名規則」を
# 二重実装しないための共有。services→handlers という既存の依存方向のまま
# importできる（handlers→servicesは禁止だが、逆方向は問題ない）。

# 各Office形式について、実際にHandlerが対象外にしている範囲を漏れなく伝える注記
# （設計書4.6「対象外の箇所は処理後レポートに注記として常に表示する」。同章の
# 表に列挙された形式別の対象外項目が、そのままここでの注記内容の根拠）。
#
# 再レビューで判明した回帰（Finding, 44bdeca以前）: 前回の修正は「数式内
# リテラル/定義名」(xlsx)・「脚注/文末脚注/コメント」(docx) という新たに
# 判明した対象外事項を注記に追加した際、設計書4.6が元々要求していた
# テキストボックス/図形内文字・埋込画像・埋込オブジェクト等の開示を誤って
# 削り落としてしまっていた。以下は新旧どちらの開示も欠落なく含む。
#
# .pptx は「シェイプ内テキスト（グループ化されたシェイプを含む）が対象外」
# という虚偽の注記だけは付けない — 表・スピーカーノートを含め実際に
# PptxHandler がマスクしているため（handlers/pptx_handler.py で確認済み）。
# ただし4.6の表が挙げる埋込画像・埋込オブジェクト・スライドマスターは
# PptxHandler が対象にしていない実際の欠落なので、pptx にも注記を付ける。
_DOCX_EXCLUSION_NOTE = (
    "テキストボックス/図形内文字・埋込画像・埋込オブジェクト・"
    "変更履歴の削除済みテキスト・脚注/文末脚注/コメントは対象外です"
)
_XLSX_EXCLUSION_NOTE = (
    "図形/テキストボックス内文字・埋込画像・埋込オブジェクト・"
    "ピボットキャッシュ・数式内の文字列リテラル・定義名は対象外です"
)
_PPTX_EXCLUSION_NOTE = "埋込画像・埋込オブジェクト・スライドマスターは対象外です"


def _numbered_output(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{MASKED_SUFFIX}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}{MASKED_SUFFIX}({n}){suffix}"
        n += 1
    return candidate


def _restored_stem(masked_stem: str) -> str:
    """マスク済みファイルの語幹から、本アプリが付与した "_masked"/"_masked(N)"
    という末尾サフィックスだけを取り除く（設計書 5章、restore_file の出力名）。

    サフィックスとして認識できない場合（本アプリ由来のファイルでない等）は、
    語幹をそのまま返す＝勝手に別の部分文字列を削らない。
    """
    m = MASKED_STEM_RE.match(masked_stem)
    return m.group("base") if m else masked_stem


def _numbered_restored_output(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}_restored{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}_restored({n}){suffix}"
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

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "Pipeline":
        """`AppConfig` から Pipeline を組み立てるファクトリ（設計書 6.1）。

        GUI 層（`gui/`）は `core/` の各検出器（`PatternDetector` 等）や
        `Detector` を直接インポートしてはならない（CLAUDE.md のアーキテクチャ
        制約: 「GUIは services/ にのみ依存し、core/・handlers/ を直接
        importしない」）。このメソッドは、その組み立て責務を Pipeline の
        公開APIとして提供することで、GUI 側が core/ に触れずに済むように
        するためのものであり、`Pipeline.__init__` が既に受け取る
        `detector`/`mode` の構成をそのまま内部で行うだけの薄いラッパー。
        """
        detector = Detector(
            [PatternDetector(), DictionaryDetector(cfg.custom_dictionary), NerDetector()],
            enabled_categories=cfg.enabled_categories,
        )
        return cls(detector=detector, mode=cfg.mask_mode)

    def analyze_text(self, text: str) -> tuple[Fragment, list[Detection]]:
        """ファイルを介さない生テキスト（クリップボード等）を、1つの
        `Fragment(location="clipboard")` と検出リストに変換して返す。

        `analyze_file` の生テキスト版。GUI 層（`gui/`）が `PreviewDialog` で
        検出結果を確認・編集するために必要だが、GUI は `core/` の検出器を
        直接インポートしてはならない（CLAUDE.md のアーキテクチャ制約）ため、
        検出器を呼ぶ責務を Pipeline の公開 API として提供する
        （`analyze_file` と同じ役割）。
        """
        fragment = Fragment(text=text, location="clipboard")
        return fragment, self._detector.detect(text)

    def mask_text(self, text: str, masker: Masker | None = None,
                  mapping_path: Path | None = None,
                  detections: list[Detection] | None = None
                  ) -> tuple[str, MappingTable, int]:
        """ファイルを介さない生テキスト（クリップボード等）をマスクする。

        戻り値は (マスク済みテキスト, 対応表, 検出件数)。検出件数を対応表の
        `entries` の有無だけから判定してはいけない —
        redact モードでは `Masker` の仕様上、対応表は検出の有無に関わらず
        常に空になる（`core/masker.py` の `test_redact_mode_no_mapping` が
        固定化している挙動）。そのため検出件数はこの戻り値で別途明示する。

        `detections` を渡すとそのリストをマスク対象に使う（内部で再検出しない）。
        `PreviewDialog` でユーザーが確認・編集した検出結果をそのままマスクする
        ためのもの。`None` のときは従来どおり内部で `detect` する（後方互換）。

        `mapping_path` を指定し、かつ token モードで実際に対応表エントリが
        生成された場合のみ、対応表CSVをそこへ書き出す。フォルダ一括処理
        （`mask_folder`）と異なり単発呼び出しのため、検出0件時にまで
        空の対応表ファイルを作る必要はない。
        """
        if detections is None:
            detections = self._detector.detect(text)
        active_count = sum(1 for d in detections if d.enabled)
        m = masker or Masker(mode=self._mode)
        m.scan_existing_tokens([text])
        masked_texts, table = m.mask_fragments([text], [detections])
        if self._mode == "token" and mapping_path is not None and table.entries:
            _atomic_write(lambda p: write_mapping(table, p), mapping_path)
        return masked_texts[0], table, active_count

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
                  masker: Masker | None = None,
                  mapping_path: Path | None = None) -> tuple[Path, FileReport]:
        """`masker`/`mapping_path` はフォルダ一括処理（mask_folder）が横断的な
        Masker と共有対応表ファイルを渡すための内部用引数（単体呼び出しでは
        両方 None のままでよい）。
        """
        handler = get_handler(path)
        if handler is None:
            raise ValueError(f"未対応の拡張子です: {path.suffix}")
        m = masker or Masker(mode=self._mode)
        m.scan_existing_tokens([f.text for f in fragments])
        masked_texts, table = m.mask_fragments(
            [f.text for f in fragments], detections)
        masked_frags = [
            Fragment(text=text, location=frag.location, encoding=frag.encoding)
            for frag, text in zip(fragments, masked_texts)
        ]

        directory = out_dir or path.parent
        out_path = _numbered_output(directory, path.stem, path.suffix)

        # 対応表をマスク済ファイルより先に確定する（設計書グローバル制約）。
        # 単体処理・フォルダ一括処理のいずれでも、この呼び出しが返す `table` は
        # 呼び出し元の Masker（専用/共有どちらでも）が保持する最新の累積対応表
        # そのものなので、常にこの時点の最新状態を書き出せば良い。
        if self._mode == "token":
            mp = mapping_path or (directory / (out_path.name + ".pmap.csv"))
            _atomic_write(lambda p: write_mapping(table, p), mp)

        _atomic_write(lambda p: handler.write_fragments(path, p, masked_frags),
                      out_path)

        # category_counts は「実際にマスクされた項目」を反映しなければ
        # ならない。渡された detections をそのまま(解決前の生の件数として)
        # 数えると、手動追加(source="manual")が既存の自動検出と重なる
        # ケースで水増しされる — 例えば自動検出 PERSON がある範囲を、手動で
        # EMAIL として重ねて選んだ場合、Masker.mask_fragments() 内部では
        # resolve_overlaps() により EMAIL 側が丸ごと破棄され実際には
        # PERSON の1項目しかマスクされないのに、ここで生の detections を
        # 数えると {"PERSON": 1, "EMAIL": 1} という存在しない項目のぶんまで
        # カウントしてしまう。Masker.mask_fragments() が内部で使うのと
        # 同じ解決結果を resolve_active_detections() 経由で取得し、それを
        # 数えることで、レポートが実際の出力と一致するようにする。
        counts: dict[str, int] = {}
        for dets in m.resolve_active_detections(
                [f.text for f in fragments], detections):
            for d in dets:
                counts[d.category] = counts.get(d.category, 0) + 1
        notes = []
        suffix = path.suffix.lower()
        if suffix == ".docx":
            notes.append(_DOCX_EXCLUSION_NOTE)
        elif suffix == ".xlsx":
            notes.append(_XLSX_EXCLUSION_NOTE)
        elif suffix == ".pptx":
            notes.append(_PPTX_EXCLUSION_NOTE)
        # .txt系には注記を追加しない（そもそもOffice固有の対象外範囲がないため）。
        return out_path, FileReport(path=path, category_counts=counts, notes=notes)

    def mask_folder(self, root: Path, out_dir: Path | None = None,
                    progress: Callable[[int, int, Path], None] | None = None
                    ) -> tuple[BatchReport, Path | None]:
        walk_result = walk(root)
        shared_masker = Masker(mode=self._mode)
        batch = BatchReport(skipped=list(walk_result.skipped))
        total = len(walk_result.supported)

        mapping_path: Path | None = None
        if self._mode == "token":
            mapping_path = root / "_folder.pmap.csv"
            # フォルダ内のどのファイルも処理する前に、まず空の対応表を確定
            # させておく。こうすることで、1件目のマスク済み出力が書かれる
            # 前から常に対応表が存在し（対応表が空フォルダでも欠落しない）、
            # 以降は各ファイル処理のたびに mask_file 側で最新の累積対応表に
            # 更新される（対応表は常にマスク済ファイル群の"先"にある状態を
            # 維持する。ループ完走を待って最後に一度だけ書く実装だと、途中で
            # 処理が中断された場合にトークンだけが存在し対応表に載っていない
            # 復元不能なマスク済ファイルが残り得るため、それを避ける）。
            _atomic_write(lambda p: write_mapping(MappingTable(entries=[]), p),
                          mapping_path)

        for i, path in enumerate(walk_result.supported, start=1):
            if progress:
                progress(i, total, path)
            try:
                fragments, detections = self.analyze_file(path)
                _, file_report = self.mask_file(
                    path, fragments, detections, out_dir=out_dir,
                    masker=shared_masker, mapping_path=mapping_path)
                batch.files.append(file_report)
            except Exception as exc:  # 個別失敗で一括処理は止めない（設計書 7章）
                batch.files.append(FileReport(
                    path=path, category_counts={}, notes=[],
                    error=f"{type(exc).__name__}: 処理できませんでした"))

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
        stem = _restored_stem(masked_path.stem)
        out_path = _numbered_restored_output(directory, stem, masked_path.suffix)
        _atomic_write(
            lambda p: handler.write_fragments(masked_path, p, fragments), out_path)
        return out_path, RestoreResult(text="", unknown_tokens=all_unknown)
