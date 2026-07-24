"""プレビュー画面（設計書 6.2）。左: ハイライト付き原文 / 右: 検出一覧。

アーキテクチャ制約（CLAUDE.md）についての注記: `..core.models` から
`CATEGORY_LABELS`/`Detection` をインポートしているが、これは違反ではない。
`Fragment`/`Detection`/`CATEGORY_LABELS` は `Pipeline.analyze_file()` が
呼び出し側へそのまま返す素のデータ型であり、GUI はそれを表示・編集する
だけで、検出器やマスカーの組み立て（禁止されている業務ロジックの再実装）は
一切行っていない。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QSplitter, QTextEdit, QVBoxLayout,
)

from ..core.models import CATEGORY_LABELS, Detection

_CATEGORY_COLORS = {
    "PERSON": "#ffd0d0", "ORG": "#d0e0ff", "LOC": "#d0ffd0",
    "PHONE": "#fff0c0", "EMAIL": "#ffe0ff", "ADDRESS": "#e0fff0",
    "POSTAL": "#f0e0d0", "MYNUMBER": "#f0e0d0", "CREDITCARD": "#f0e0d0",
    "CUSTOM": "#e0e0e0",
}

# ラベルが重複するカテゴリ（POSTAL/MYNUMBER/CREDITCARD は全て「番号」）が
# 手動追加メニューに複数現れると、ユーザーには見分けの付かない同一の
# メニュー項目が並ぶだけになる（Task 18 調査3）。かつ
# `core/mapping_io.py` の `_LABEL_TO_CATEGORY` が既に「同じラベルの
# カテゴリはCATEGORY_LABELS宣言順で最初に出てきたものを代表として扱う」
# という割り切りを採用している（Task 16 レビューで既知・許容済みの
# 割り切り）ため、手動追加メニューでも同じ代表選びの規則に揃える。
# これにより「メニューでどれを選んでも復元時の代表カテゴリと一致する」
# 一貫性が保てる。
def _representative_categories() -> dict[str, str]:
    label_to_category: dict[str, str] = {}
    for category, label in CATEGORY_LABELS.items():
        label_to_category.setdefault(label, category)
    return label_to_category


class PreviewDialog(QDialog):
    def __init__(self, fragments, detections, parent=None):
        super().__init__(parent)
        self.setWindowTitle("検出結果の確認")
        self.resize(900, 600)
        self.fragments = fragments
        self.detections = detections  # list[list[Detection]] を直接編集する

        splitter = QSplitter(Qt.Horizontal)

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_view.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.text_view)

        self.list_view = QListWidget()
        self.list_view.itemChanged.connect(self._on_item_toggled)
        splitter.addWidget(self.list_view)
        splitter.setSizes([600, 300])

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("この内容でマスク実行")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(buttons)
        self._refresh()

    # フラグメント境界をまたがない前提で、全断片を連結表示する
    def _refresh(self):
        self.text_view.clear()
        self.list_view.blockSignals(True)
        self.list_view.clear()
        offset = 0
        self._offsets = []
        full = []
        for frag in self.fragments:
            self._offsets.append(offset)
            full.append(frag.text)
            offset += len(frag.text) + 1  # 区切りの改行分
        self.text_view.setPlainText("\n".join(full))

        cursor = self.text_view.textCursor()
        for fi, dets in enumerate(self.detections):
            for d in dets:
                item = QListWidgetItem(
                    f"[{CATEGORY_LABELS[d.category]}] {d.text}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if d.enabled else Qt.Unchecked)
                item.setData(Qt.UserRole, (fi, d))
                self.list_view.addItem(item)
                if d.enabled:
                    fmt = QTextCharFormat()
                    fmt.setBackground(QColor(_CATEGORY_COLORS[d.category]))
                    cursor.setPosition(self._offsets[fi] + d.start)
                    cursor.setPosition(self._offsets[fi] + d.end,
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(fmt)
        self.list_view.blockSignals(False)

    def _on_item_toggled(self, item):
        fi, d = item.data(Qt.UserRole)
        d.enabled = item.checkState() == Qt.Checked
        self._refresh()

    def _context_menu(self, pos):
        cursor = self.text_view.textCursor()
        selected = cursor.selectedText()
        if not selected:
            return
        menu = QMenu(self)
        # ラベル単位で重複排除した代表カテゴリ1つにつき1メニュー項目を出す
        # （調査3: POSTAL/MYNUMBER/CREDITCARDが同じ「番号」で3つ並ぶのを防ぐ）。
        for label, category in sorted(_representative_categories().items()):
            action = QAction(f"「{selected}」を{label}として追加", menu)
            action.triggered.connect(
                lambda _=False, c=category: self._add_manual(cursor, c))
            menu.addAction(action)
        menu.exec(self.text_view.mapToGlobal(pos))

    def _add_manual(self, cursor, category):
        sel_start, sel_end = cursor.selectionStart(), cursor.selectionEnd()
        # どの断片か特定（offsetsは各断片の開始位置＝直前までの断片＋区切り
        # 改行の累計なので、sel_start以下で最大のoffsetを持つ断片を選ぶ）。
        for fi in reversed(range(len(self._offsets))):
            if sel_start >= self._offsets[fi]:
                frag_start = sel_start - self._offsets[fi]
                frag_end = sel_end - self._offsets[fi]
                frag_len = len(self.fragments[fi].text)
                # 調査2: 選択範囲が断片境界（連結時に挿入した改行、または
                # 次の断片）をまたいでいないかを確認する。またいでいる場合、
                # 元のコード（コメントで「またがない前提」と認めつつ実際の
                # ガードがなかった）のように黙って frag_end を断片長で
                # クリップしてしまうと、ユーザーの選択範囲の一部が記録
                # されないまま Detection.text が短く確定してしまい、
                # 気づかれないままマスク漏れにつながる。ここでは黙って
                # 切り詰めず、操作を中止してユーザーに伝える。
                if frag_start >= frag_len or frag_end > frag_len:
                    QMessageBox.warning(
                        self, "追加できません",
                        "選択範囲が段落／セルなどの区切りをまたいでいます。"
                        "1つの区切り内に収まるよう選び直してください。")
                    return
                text = self.fragments[fi].text[frag_start:frag_end]
                if text:
                    new_detection = Detection(
                        text=text, category=category,
                        start=frag_start, end=frag_end, source="manual")
                    # 調査1: この時点で既存の有効な検出と重なっていても、
                    # データ破壊は core/masker.py 側の resolve_overlaps に
                    # よって防がれる（優先順位: 手動 > 辞書 > パターン > NER、
                    # 同順位なら範囲が長い方）。ただし、ユーザーがそのことに
                    # 気づかないまま「追加したのに何も変わらなかった」と
                    # 感じないよう、重なりがある場合はここで一言伝える。
                    overlap = self._enabled_overlap(fi, new_detection)
                    self.detections[fi].append(new_detection)
                    if overlap is not None:
                        QMessageBox.information(
                            self, "既存の検出と重複しています",
                            f"選択範囲は既存の検出「[{CATEGORY_LABELS[overlap.category]}] "
                            f"{overlap.text}」と重なっています。マスク実行時に、"
                            "範囲が広い方（同じ範囲なら今回の手動追加）が優先され、"
                            "重複しない部分は自動的に解決されます。")
                break
        self._refresh()

    @staticmethod
    def _overlaps(a_start, a_end, b_start, b_end) -> bool:
        return a_start < b_end and b_start < a_end

    def _enabled_overlap(self, fi: int, new_detection: Detection) -> Detection | None:
        """同一断片内で、追加しようとしている検出と重なる既存の有効な検出が
        あれば1件返す（ユーザーへの警告表示用。実際のマスク時の重複解決は
        core/masker.py が担う）。"""
        for existing in self.detections[fi]:
            if existing.enabled and self._overlaps(
                    new_detection.start, new_detection.end,
                    existing.start, existing.end):
                return existing
        return None
