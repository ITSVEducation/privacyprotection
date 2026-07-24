"""マスク・復元のオーケストレーション（設計書 5章）。"""
from __future__ import annotations

import os
import re
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
from ..handlers.folder_walker import walk
from ..handlers.registry import get_handler
from .report import BatchReport, FileReport

MASKED_SUFFIX = "_masked"

# mask_file が付与する連番付きサフィックス（"_masked" または "_masked(N)"）を
# 語幹の「末尾」からのみ取り除くためのパターン。単純な str.replace() は
# 語幹の途中に偶然 "_masked" を含む名前（本アプリ由来でないファイル、例:
# "already_masked_by_someone_else.txt"）まで壊してしまうため使わない。
_MASKED_STEM_RE = re.compile(r"^(?P<base>.*)" + re.escape(MASKED_SUFFIX) + r"(?:\(\d+\))?$")


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
    m = _MASKED_STEM_RE.match(masked_stem)
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

    def mask_text(self, text: str, masker: Masker | None = None,
                  mapping_path: Path | None = None) -> tuple[str, MappingTable, int]:
        """ファイルを介さない生テキスト（クリップボード等）をマスクする。

        戻り値は (マスク済みテキスト, 対応表, 検出件数)。検出件数を対応表の
        `entries` の有無だけから判定してはいけない —
        redact モードでは `Masker` の仕様上、対応表は検出の有無に関わらず
        常に空になる（`core/masker.py` の `test_redact_mode_no_mapping` が
        固定化している挙動）。そのため検出件数はこの戻り値で別途明示する。

        `mapping_path` を指定し、かつ token モードで実際に対応表エントリが
        生成された場合のみ、対応表CSVをそこへ書き出す。フォルダ一括処理
        （`mask_folder`）と異なり単発呼び出しのため、検出0件時にまで
        空の対応表ファイルを作る必要はない。
        """
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
        masked_frags = []
        for frag, text in zip(fragments, masked_texts):
            frag_copy = Fragment(text=text, location=frag.location)
            frag_copy.encoding = getattr(frag, "encoding", None)
            masked_frags.append(frag_copy)

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
