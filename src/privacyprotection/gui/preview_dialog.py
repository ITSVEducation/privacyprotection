"""プレビュー画面（設計書 6.2）。左: ハイライト付き原文 / 右: 検出一覧。

アーキテクチャ制約（CLAUDE.md）についての注記: `..core.models` から
`CATEGORY_LABELS`/`Detection` をインポートしているが、これは違反ではない。
`Fragment`/`Detection`/`CATEGORY_LABELS` は `Pipeline.analyze_file()` /
`Pipeline.analyze_text()` が呼び出し側へそのまま返す素のデータ型であり、GUI は
それを表示・編集するだけで、検出器やマスカーの組み立て（禁止されている業務
ロジックの再実装）は一切行っていない。

左ペインの直接操作（2026-07-24 設計）:
- ドラッグで範囲選択して離す → その範囲を「カスタム」の手動検出として即追加。
- ハイライト部分を（選択せず）クリック → その検出を無効化（右リストの
  チェックを外すのと同義。再有効化は右リストのチェックで行う）。
- カテゴリ変更は右リスト項目の右クリックメニューで行う。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QSplitter, QTextEdit, QVBoxLayout,
)

from ..core.models import CATEGORY_LABELS, LABEL_TO_CATEGORY, Detection
from ..core.spans import find_occurrences

_CATEGORY_COLORS = {
    "PERSON": "#ffd0d0", "ORG": "#d0e0ff", "LOC": "#d0ffd0",
    "PHONE": "#fff0c0", "EMAIL": "#ffe0ff", "ADDRESS": "#e0fff0",
    "POSTAL": "#f0e0d0", "MYNUMBER": "#f0e0d0", "CREDITCARD": "#f0e0d0",
    "CUSTOM": "#e0e0e0",
}
_DEFAULT_CATEGORY_COLOR = _CATEGORY_COLORS["CUSTOM"]

# ドラッグで手動追加する既定カテゴリ。範囲を選んで離すだけで即マスク対象に
# できるよう「カスタム」で追加し、別カテゴリにしたいときは右リストの右クリック
# メニューで変更する（2026-07-24 設計）。
_DRAG_ADD_CATEGORY = "CUSTOM"

# ラベルが重複するカテゴリ（POSTAL/MYNUMBER/CREDITCARD は全て「番号」）が
# カテゴリ変更メニューに複数現れると、ユーザーには見分けの付かない同一の
# メニュー項目が並ぶだけになる。core/models.py の `LABEL_TO_CATEGORY` が既に
# 「同じラベルのカテゴリはCATEGORY_LABELS宣言順で最初に出てきたものを代表と
# して扱う」という割り切りを採用しているため、メニューでも同じ代表選びの
# 規則に揃える（どれを選んでも復元時の代表カテゴリと一致する一貫性が保てる）。


class _InteractiveTextView(QTextEdit):
    """読み取り専用のまま、左ボタンを離した時の操作を親へ通知するビュー。

    選択あり（ドラッグ／ダブルクリック）→ `on_select(start, end)`＝範囲を
    手動検出として追加。選択なし（単純クリック）→ `on_click(pos)`＝その位置の
    検出を無効化。QTextEdit では単純クリックで選択が消えてカーソルだけ移動する
    ため、離した時点で選択の有無を見ればドラッグとクリックを確実に区別できる。
    """

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


class PreviewDialog(QDialog):
    def __init__(self, fragments, detections, parent=None):
        super().__init__(parent)
        self.setWindowTitle("検出結果の確認")
        self.resize(900, 600)
        self.fragments = fragments
        self.detections = detections  # list[list[Detection]] を直接編集する

        # 「同じ語をまとめて扱う」モード（既定OFF、2026-07-27 設計）。表示粒度と
        # 操作粒度を1つのトグルでまとめて切り替える（集約表示なのにクリックは
        # 1箇所だけ、といった不整合を作らないため）。
        self.group_same = QCheckBox("同じ語をまとめて扱う（全出現をまとめてマスク）")
        self.group_same.toggled.connect(self._refresh)

        splitter = QSplitter(Qt.Horizontal)

        self.text_view = _InteractiveTextView(
            on_select=self._on_drag_select, on_click=self._on_click_disable)
        splitter.addWidget(self.text_view)

        self.list_view = QListWidget()
        self.list_view.itemChanged.connect(self._on_item_toggled)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._list_context_menu)
        splitter.addWidget(self.list_view)
        splitter.setSizes([600, 300])

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("この内容でマスク実行")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.group_same)
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

        # ハイライトはモードに関わらず、有効な検出のスパンすべてに色を付ける。
        cursor = self.text_view.textCursor()
        for fi, dets in enumerate(self.detections):
            for d in dets:
                if d.enabled:
                    fmt = QTextCharFormat()
                    # 手編集のconfig.jsonに由来する固定10種以外のカテゴリでも
                    # KeyErrorで落ちないよう、色が定義されていなければCUSTOMと
                    # 同じ色にフォールバックする。
                    fmt.setBackground(QColor(
                        _CATEGORY_COLORS.get(d.category, _DEFAULT_CATEGORY_COLOR)))
                    cursor.setPosition(self._offsets[fi] + d.start)
                    cursor.setPosition(self._offsets[fi] + d.end,
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(fmt)

        if self.group_same.isChecked():
            self._populate_list_grouped()
        else:
            self._populate_list_individual()
        self.list_view.blockSignals(False)

    def _populate_list_individual(self):
        """検出スパン1件につき1行（既定）。"""
        for fi, dets in enumerate(self.detections):
            for d in dets:
                item = QListWidgetItem(
                    f"[{CATEGORY_LABELS.get(d.category, d.category)}] {d.text}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if d.enabled else Qt.Unchecked)
                item.setData(Qt.UserRole, [d])
                self.list_view.addItem(item)

    def _populate_list_grouped(self):
        """同じ (値, カテゴリ) を1行に集約し、件数を ×N で示す。

        チェック状態はグループ内に有効な検出が1つでもあれば Checked とする
        （このモードではチェック操作もグループ全体に及ぶため、混在状態は
        操作直後には生じない。OFFで一部だけ無効化してからONへ切り替えた
        場合にのみ混在があり得るが、その場合も「1つでも有効なら
        Checked」で表示し、チェックを外せばグループ全体が無効になる）。
        """
        groups: dict[tuple[str, str], list[Detection]] = {}
        for dets in self.detections:
            for d in dets:
                groups.setdefault((d.text, d.category), []).append(d)
        for (text, category), members in groups.items():
            label = CATEGORY_LABELS.get(category, category)
            suffix = f"  ×{len(members)}" if len(members) > 1 else ""
            item = QListWidgetItem(f"[{label}] {text}{suffix}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if any(m.enabled for m in members) else Qt.Unchecked)
            item.setData(Qt.UserRole, members)
            self.list_view.addItem(item)

    def _on_item_toggled(self, item):
        # UserRole には対象の Detection 群が入っている（個別モードなら1件、
        # まとめモードならその値の全出現）。どちらも同じ扱いで済む。
        enabled = item.checkState() == Qt.Checked
        for d in item.data(Qt.UserRole):
            d.enabled = enabled
        self._refresh()

    # --- 左ペインの直接操作 --------------------------------------------
    def _on_drag_select(self, sel_start: int, sel_end: int):
        """ドラッグ選択された範囲を「カスタム」の手動検出として追加する。

        「同じ語をまとめて扱う」ONのときは、選択した語と同じ語を全断片から
        探して全出現ぶん追加する（設計書 §6: 全出現展開はユーザーが明示的に
        選んだ語に限定する）。
        """
        self._add_manual(sel_start, sel_end, _DRAG_ADD_CATEGORY)

    def _on_click_disable(self, pos: int):
        """連結テキスト上の位置 `pos` の検出を無効化する。

        個別モード（既定）ではクリックした1箇所だけ。「同じ語をまとめて扱う」
        ONのときは、その位置の検出と同じ値を持つ有効な検出を全断片からまとめて
        無効化する（一覧の集約行と粒度を一致させる）。

        複数の有効な検出が重なる位置では、最後に追加された（＝最前面に
        見えている）ものを対象にする。無効化するとハイライトが消え、右リストの
        チェックも自動的に外れる（_refresh がチェック状態を d.enabled から
        再構築するため）。再有効化は右リストのチェックで行う。
        """
        target = self._detection_at(pos)
        if target is None:
            return
        if self.group_same.isChecked():
            for dets in self.detections:
                for d in dets:
                    if d.enabled and d.text == target.text:
                        d.enabled = False
        else:
            target.enabled = False
        self._refresh()

    def _detection_at(self, pos: int) -> Detection | None:
        """連結テキスト上の位置 `pos` にある有効な検出を1件返す（無ければ None）。"""
        for fi in reversed(range(len(self._offsets))):
            if pos >= self._offsets[fi]:
                frag_pos = pos - self._offsets[fi]
                for d in reversed(self.detections[fi]):
                    if d.enabled and d.start <= frag_pos < d.end:
                        return d
                return None
        return None

    def _list_context_menu(self, pos):
        item = self.list_view.itemAt(pos)
        if item is None:
            return
        targets = item.data(Qt.UserRole)
        menu = QMenu(self)
        # ラベル単位で重複排除した代表カテゴリ1つにつき1項目を出す
        # （POSTAL/MYNUMBER/CREDITCARD が同じ「番号」で3つ並ぶのを防ぐ）。
        for label, category in sorted(LABEL_TO_CATEGORY.items()):
            action = QAction(f"「{label}」に変更", menu)
            action.triggered.connect(
                lambda _=False, dets=targets, c=category:
                    self._change_category(dets, c))
            menu.addAction(action)
        menu.exec(self.list_view.mapToGlobal(pos))

    def _change_category(self, detections: list[Detection], category: str):
        # 個別モードなら1件、まとめモードならその値の全出現に適用される
        # （UserRole に入っている対象がモードごとに異なるだけ）。
        for d in detections:
            d.category = category
        self._refresh()

    def _add_manual(self, sel_start: int, sel_end: int, category: str):
        # どの断片か特定（offsetsは各断片の開始位置＝直前までの断片＋区切り
        # 改行の累計なので、sel_start以下で最大のoffsetを持つ断片を選ぶ）。
        for fi in reversed(range(len(self._offsets))):
            if sel_start >= self._offsets[fi]:
                frag_start = sel_start - self._offsets[fi]
                frag_end = sel_end - self._offsets[fi]
                frag_len = len(self.fragments[fi].text)
                # 選択範囲が断片境界（連結時に挿入した改行、または次の断片）を
                # またいでいないかを確認する。またいでいる場合、黙って frag_end を
                # 断片長でクリップすると、ユーザーの選択範囲の一部が記録されない
                # まま Detection.text が短く確定してしまい、気づかれないまま
                # マスク漏れにつながる。ここでは黙って切り詰めず、操作を中止して
                # ユーザーに伝える（マスク漏れに直結するため警告は残す）。
                if frag_start >= frag_len or frag_end > frag_len:
                    QMessageBox.warning(
                        self, "追加できません",
                        "選択範囲が段落／セルなどの区切りをまたいでいます。"
                        "1つの区切り内に収まるよう選び直してください。")
                    return
                text = self.fragments[fi].text[frag_start:frag_end]
                if text:
                    # この時点で既存の有効な検出と重なっていても、データ破壊は
                    # core/masker.py 側の resolve_overlaps によって防がれる
                    # （優先順位: 手動 > 辞書 > パターン > NER、同順位なら範囲が
                    # 長い方）。重なりの結果はハイライトに反映されて見えるため、
                    # ドラッグごとに情報ダイアログは出さない（軽快さを優先）。
                    if self.group_same.isChecked():
                        self._add_all_occurrences(text, category)
                    else:
                        self._add_one(fi, text, frag_start, frag_end, category)
                break
        self._refresh()

    def _add_one(self, fi: int, text: str, start: int, end: int, category: str):
        """1箇所ぶんの手動検出を追加する（同じスパンが既にあれば追加しない）。"""
        for existing in self.detections[fi]:
            if existing.start == start and existing.end == end:
                # 同じ範囲を二重に持つと、解決後も一覧に同じ行が並ぶだけで
                # 意味がない（重なり自体は resolve_overlaps が解決する）。
                # 既存が無効化されていた場合はここで復活させる。
                existing.enabled = True
                return
        self.detections[fi].append(Detection(
            text=text, category=category,
            start=start, end=end, source="manual"))

    def _add_all_occurrences(self, value: str, category: str):
        """全断片から `value` の完全一致・全出現を探して手動検出を追加する
        （「同じ語をまとめて扱う」ONのときのドラッグ追加）。"""
        for fi, frag in enumerate(self.fragments):
            for start, end in find_occurrences(frag.text, value):
                self._add_one(fi, value, start, end, category)
