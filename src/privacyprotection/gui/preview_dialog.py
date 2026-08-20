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


def collect_manual_entries(
        detections: list[list[Detection]]) -> dict[str, str]:
    """有効な手動追加検出から辞書登録用の {語句: カテゴリ} を組み立てる。

    マスク実行確定時にカスタム辞書へ自動登録する対象の選別（設計書 §4.2）。
    対象は enabled かつ source == "manual" のみ。自動検出（pattern/ner/
    dictionary）を含めると、一度でも検出された語がすべて辞書に固定されて
    しまい、カテゴリOFFで検出を止める手段が効かなくなるため含めない。
    同じ語句が複数あれば最初に現れたものが勝つ。
    """
    entries: dict[str, str] = {}
    for dets in detections:
        for d in dets:
            if d.enabled and d.source == "manual":
                entries.setdefault(d.text, d.category)
    return entries


def build_display_map(text: str) -> tuple[str, list[int], list[int]]:
    """断片テキストを QTextEdit 表示用に正規化し、位置の相互変換表を返す。

    戻り値は (表示テキスト, py2disp, disp2py)。
    - `py2disp[i]`  … Python文字列オフセット i に対応する表示位置
    - `disp2py[j]`  … 表示位置 j に対応する Python文字列オフセット
    いずれも終端（len）を含むため長さは元＋1／表示長＋1。

    **なぜ必要か**: `QTextDocument.setPlainText()` は "\\r\\n" と単独 "\\r" を
    それぞれ1つの段落区切り（＝1文字ぶんの位置）に畳む。一方 Python の
    `Detection.start/end` は元テキストのオフセットなので、"\\r\\n" を含む
    テキスト（Windowsのクリップボードでは一般的）では改行の数だけ両者の位置が
    ずれていく。この変換を挟まずに `cursor.setPosition(d.start)` すると、
    ハイライトが本来と無関係な箇所に付き（改行が増えるほど広範囲にずれる）、
    さらにドラッグ選択で得た表示位置をそのまま Python オフセットとして
    扱うと、ユーザーが選んだ語とは別の範囲が手動検出として登録されてしまう
    ＝マスク漏れになる。表示と検出位置の対応を常にこの表経由にする。
    """
    out: list[str] = []
    py2disp = [0] * (len(text) + 1)
    disp2py: list[int] = []
    i = 0
    while i < len(text):
        if text[i] == "\r":
            py2disp[i] = len(out)
            if i + 1 < len(text) and text[i + 1] == "\n":
                # CRLF: 2文字が表示上1文字（段落区切り）に畳まれる
                py2disp[i + 1] = len(out)
                consumed = 2
            else:
                consumed = 1
            disp2py.append(i)
            out.append("\n")
            i += consumed
        else:
            py2disp[i] = len(out)
            disp2py.append(i)
            out.append(text[i])
            i += 1
    py2disp[len(text)] = len(out)
    disp2py.append(len(text))
    return "".join(out), py2disp, disp2py


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
        # 再描画のたびに先頭へ戻ると「どこを編集していたか」を見失うため、
        # スクロール位置を保存して復元する（ドラッグ追加・クリック無効化は
        # いずれもこの再描画を伴うため、保存しないと毎回先頭へ飛ぶ）。
        vbar = self.text_view.verticalScrollBar()
        hbar = self.text_view.horizontalScrollBar()
        saved_v, saved_h = vbar.value(), hbar.value()

        # 本文を入れ直す前に文字書式をリセットする。ハイライト部分をクリック／
        # ドラッグすると QTextEdit の currentCharFormat がその位置の書式（＝
        # カテゴリ色の背景）になり、この後の setPlainText() がその書式のまま
        # 全文を挿入するため、左ペイン全体が同じ色で塗り潰されてしまう。
        # setPlainText() の「後」にリセットしても手遅れなので、必ず前に行う。
        self.text_view.setCurrentCharFormat(QTextCharFormat())
        self.text_view.clear()
        self.list_view.blockSignals(True)
        self.list_view.clear()

        # 表示テキストと位置変換表を断片ごとに用意する。self._offsets は
        # 「表示位置」空間での各断片の開始位置（Pythonオフセットではない）。
        offset = 0
        self._offsets = []
        self._maps = []
        full = []
        for frag in self.fragments:
            disp, py2disp, disp2py = build_display_map(frag.text)
            self._offsets.append(offset)
            self._maps.append((py2disp, disp2py))
            full.append(disp)
            offset += len(disp) + 1  # 区切りの改行分
        self.text_view.setPlainText("\n".join(full))

        # ハイライトはモードに関わらず、有効な検出のスパンすべてに色を付ける。
        cursor = self.text_view.textCursor()
        for fi, dets in enumerate(self.detections):
            py2disp, _ = self._maps[fi]
            for d in dets:
                if d.enabled:
                    fmt = QTextCharFormat()
                    # 手編集のconfig.jsonに由来する固定10種以外のカテゴリでも
                    # KeyErrorで落ちないよう、色が定義されていなければCUSTOMと
                    # 同じ色にフォールバックする。
                    fmt.setBackground(QColor(
                        _CATEGORY_COLORS.get(d.category, _DEFAULT_CATEGORY_COLOR)))
                    # Pythonオフセット→表示位置へ変換してから着色する
                    # （変換しないとCRLFを含むテキストで着色位置がずれる）。
                    base = self._offsets[fi]
                    cursor.setPosition(base + py2disp[d.start])
                    cursor.setPosition(base + py2disp[d.end],
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(fmt)

        # 着色でカーソルに残った文字書式が、この後の操作へ持ち越されないよう
        # 明示的にリセットしておく。
        self.text_view.setCurrentCharFormat(QTextCharFormat())
        vbar.setValue(saved_v)
        hbar.setValue(saved_h)

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

        ただし既存の検出の上での1文字以下の選択は、クリックしようとして数
        ピクセル動いてしまった操作とみなし、追加ではなくON/OFFの切り替えとして
        扱う（クリックのつもりが1文字だけの無意味なマスクを増やしてしまうのを
        防ぐ。そのような包含された検出はどのみち resolve_overlaps で破棄される）。
        """
        if sel_end - sel_start <= 1 and self._detection_at(sel_start) is not None:
            self._on_click_disable(sel_start)
            return
        self._add_manual(sel_start, sel_end, _DRAG_ADD_CATEGORY)

    def _on_click_disable(self, pos: int):
        """クリック位置の検出のマスクON/OFFを切り替える（トグル）。

        有効な検出の上をクリックすれば無効化し、無効化済みの検出の上を
        もう一度クリックすれば再び有効化する。無効化した箇所はハイライトが
        消えるが範囲自体は保持しているため、同じ場所をクリックすれば戻せる
        （右リストのチェックを付け外すのと同じ結果になる）。

        個別モード（既定）ではクリックした1箇所だけ。「同じ語をまとめて扱う」
        ONのときは、同じ値を持つ検出を全断片からまとめて切り替える
        （一覧の集約行と粒度を一致させる）。

        複数の検出が重なる位置では、有効なものを優先し、なければ無効化済みの
        ものを対象にする（同順ならいずれも最後に追加されたもの＝最前面）。
        """
        target = self._detection_at(pos)
        if target is None:
            return
        new_state = not target.enabled
        if self.group_same.isChecked():
            for dets in self.detections:
                for d in dets:
                    if d.text == target.text:
                        d.enabled = new_state
        else:
            target.enabled = new_state
        self._refresh()

    def _detection_at(self, pos: int) -> Detection | None:
        """表示位置 `pos` にある検出を1件返す（無ければ None）。

        有効な検出を優先して返し、無ければ無効化済みの検出を返す（クリックでの
        再有効化を可能にするため）。いずれも同じ位置に複数あれば最後に追加
        されたものを選ぶ。
        """
        fi, frag_pos = self._locate(pos)
        if fi is None:
            return None
        fallback: Detection | None = None
        for d in reversed(self.detections[fi]):
            if d.start <= frag_pos < d.end:
                if d.enabled:
                    return d
                if fallback is None:
                    fallback = d
        return fallback

    def _locate(self, pos: int) -> tuple[int | None, int]:
        """表示位置 `pos` を (断片index, その断片内のPythonオフセット) に変換する。

        断片が見つからない、または断片の区切り（連結時に挿入した改行）上を
        指している場合は (None, 0) を返す。
        """
        for fi in reversed(range(len(self._offsets))):
            if pos >= self._offsets[fi]:
                disp_pos = pos - self._offsets[fi]
                _, disp2py = self._maps[fi]
                if disp_pos >= len(disp2py):
                    return None, 0  # 断片間の区切り改行
                return fi, disp2py[disp_pos]
        return None, 0

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
        """表示位置で指定された選択範囲を手動検出として追加する。

        `sel_start`/`sel_end` は QTextEdit 上の表示位置なので、必ず
        `build_display_map` の変換表を通して Python オフセットへ直してから
        `Detection` にする（直接使うと CRLF を含むテキストで、ユーザーが
        選んだ語とは別の範囲が登録されてしまう）。
        """
        fi, frag_start = self._locate(sel_start)
        # 終端は「その位置の直前まで」を意味するため、範囲の最後の文字から
        # 求める（sel_end 自体は次の文字の位置＝断片外を指すことがある）。
        end_fi, last_char_start = self._locate(max(sel_end - 1, sel_start))
        if fi is None:
            return
        # 選択範囲が断片境界（連結時に挿入した改行、または次の断片）を
        # またいでいないかを確認する。またいでいる場合、黙って範囲を断片長で
        # クリップすると、ユーザーの選択範囲の一部が記録されないまま
        # Detection.text が短く確定してしまい、気づかれないままマスク漏れに
        # つながる。ここでは黙って切り詰めず、操作を中止してユーザーに伝える。
        if end_fi != fi:
            QMessageBox.warning(
                self, "追加できません",
                "選択範囲が段落／セルなどの区切りをまたいでいます。"
                "1つの区切り内に収まるよう選び直してください。")
            return
        frag_text = self.fragments[fi].text
        # last_char_start は範囲内最後の文字の開始オフセット。CRLF のような
        # 複数文字が表示上1文字に畳まれている場合も含め、その文字の終端まで
        # を範囲に含める。
        frag_end = last_char_start + 1
        if frag_text[last_char_start:last_char_start + 2] == "\r\n":
            frag_end = last_char_start + 2
        text = frag_text[frag_start:frag_end]
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

    def accepted_manual_entries(self) -> dict[str, str]:
        """有効な手動追加検出の {語句: カテゴリ}。呼び出し元が Accepted 後に
        カスタム辞書へ登録するために使う（永続化はダイアログの責務外）。"""
        return collect_manual_entries(self.detections)
