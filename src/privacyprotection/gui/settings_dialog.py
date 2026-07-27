"""設定画面（設計書 6.3）。

アーキテクチャ制約（CLAUDE.md）についての注記: `..core.models` から
`CATEGORY_LABELS` をインポートしているが、これは違反ではない
（`gui/preview_dialog.py` と同じ理由）。`CATEGORY_LABELS` は種別コード→
日本語ラベルの固定語彙という素のデータであり、検出器やマスカーの組み立て
（GUI層で禁止されている業務ロジックの再実装）ではない。検出器の組み立ては
これまで通り `services/pipeline.py` の `Pipeline.from_config()` に閉じている。

画面構成（2026-07-27）: 「検出カテゴリ」と「カスタム辞書」の2タブ。以前は
1画面へ縦積みしていたため辞書の表が狭く、そもそも辞書を編集できることに
気づきにくかった。辞書タブでは行の削除と重複語句の検知も行う。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import AppConfig, export_dictionary_csv, import_dictionary_csv
from ..core.models import CATEGORY_LABELS, LABEL_TO_CATEGORY

# カテゴリ ON/OFF トグルの対象。CUSTOM はここに含めない — CUSTOM は自動検出器
# (pattern/ner) が生成することのないカテゴリで、専らカスタム辞書由来にしか
# 付かない（`core/patterns.py`/`core/ner.py` を確認済み）。「自動検出のON/OFF」
# という本設定の意味に対して、CUSTOM用のトグルを置いても切り替える対象がない。
_TOGGLE_CATEGORIES = ["PERSON", "ORG", "LOC", "PHONE", "EMAIL",
                      "ADDRESS", "POSTAL", "MYNUMBER", "CREDITCARD"]

# カスタム辞書テーブルの「種別」列で選べる選択肢（ラベル単位で重複排除）。
# POSTAL/MYNUMBER/CREDITCARD は全て日本語ラベル「番号」を共有しているため、
# 選択肢としては「番号」を1つだけ出し、選ぶと core/models.py の
# LABEL_TO_CATEGORY による代表カテゴリ（宣言順で最初に出てくるもの＝
# POSTAL）に解決される。この「ラベル単位で重複排除し代表カテゴリに解決する」
# 規則は `core/mapping_io.py` や `gui/preview_dialog.py` と共有の、既存の
# 割り切り（Task 16レビューで承認済み、最終レビュー Finding 6 で
# core/models.py へ一本化）であり、ここで新たに導入したものではない。
_CATEGORY_LABEL_CHOICES = sorted(LABEL_TO_CATEGORY)

_DICT_HELP = (
    "ここに登録した語句は、上の「検出カテゴリ」のON/OFFに関わらず必ずマスク"
    "されます。文中に現れれば常に一致します（長い語句が優先）。"
)


class SettingsDialog(QDialog):
    """設定画面。OKで渡された `config`（`AppConfig`）を直接更新する。
    呼び出し元（`MainWindow._open_settings`）が `save_config` で永続化する。
    """

    # タブの並び順。呼び出し元が「辞書を開く」導線から
    # `SettingsDialog(cfg, initial_tab=SettingsDialog.TAB_DICTIONARY)` と
    # 指定できるよう定数にしておく（インデックス直書きを散らさない）。
    TAB_CATEGORIES = 0
    TAB_DICTIONARY = 1

    def __init__(self, config: AppConfig, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(620, 560)
        self._config = config

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_category_tab(config), "検出カテゴリ")
        self.tabs.addTab(self._build_dictionary_tab(config), "カスタム辞書")
        self.tabs.setCurrentIndex(initial_tab)
        layout.addWidget(self.tabs, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- タブ構築 --------------------------------------------------------
    def _build_category_tab(self, config: AppConfig) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        self._cat_checks: dict[str, QCheckBox] = {}
        for cat in _TOGGLE_CATEGORIES:
            cb = QCheckBox(f"{CATEGORY_LABELS[cat]}（{cat}）")
            cb.setChecked(cat in config.enabled_categories)
            self._cat_checks[cat] = cb
            tab_layout.addWidget(cb)
        tab_layout.addStretch()
        return tab

    def _build_dictionary_tab(self, config: AppConfig) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        help_label = QLabel(_DICT_HELP)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #555;")
        tab_layout.addWidget(help_label)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["語句", "種別"])
        # 語句列を伸ばして表を全面に使う（従来は狭くて読みづらかった）。
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        # 行削除を選択操作で行うため、行単位・複数選択にする。
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for word, dict_cat in config.custom_dictionary.items():
            # 手編集のconfig.jsonが固定10種以外のカテゴリを持っていても
            # ここでKeyErrorにせず、コンボボックス側で「カスタム」に
            # フォールバックさせる（Finding 7。フォールバック先の文字列は
            # _CATEGORY_LABEL_CHOICES に存在しないため、_append_row内の
            # findText()が-1を返し、既定の「カスタム」選択になる）。
            self._append_row(word, CATEGORY_LABELS.get(dict_cat, dict_cat))
        tab_layout.addWidget(self.table, stretch=1)

        btn_row = QHBoxLayout()
        for label, fn in [("行を追加", self._add_row),
                          ("選択行を削除", self._delete_rows),
                          ("CSVインポート", self._import_csv),
                          ("CSVエクスポート", self._export_csv)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btn_row.addWidget(b)
        tab_layout.addLayout(btn_row)
        return tab

    # --- 辞書テーブル操作 ------------------------------------------------
    def _append_row(self, word: str = "", label: str = "カスタム") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(word))

        # 種別列は自由記述ではなく固定語彙からのプルダウン選択にする
        # （Task 19 調査2）。自由記述だと、有効な種別ラベルを知らないと
        # 何を入力すればよいか分からず（発見しづらい）、かつ打ち間違い
        # （例:「人明」）が `LABEL_TO_CATEGORY.get(label, "CUSTOM")` に
        # より無警告のまま「カスタム」へ化けてしまい、ユーザーが意図した
        # 種別と違うものに黙って変わっても気づけない。QComboBoxにすることで
        # 無効な入力自体を構造的に不可能にする。
        combo = QComboBox()
        combo.addItems(_CATEGORY_LABEL_CHOICES)
        idx = combo.findText(label)
        combo.setCurrentIndex(idx if idx >= 0 else combo.findText("カスタム"))
        self.table.setCellWidget(r, 1, combo)

    def _add_row(self) -> None:
        self._append_row()
        # 追加した行がすぐ編集できるよう、語句セルへフォーカスを移す。
        r = self.table.rowCount() - 1
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))

    def _delete_rows(self) -> None:
        """選択されている行をまとめて削除する。

        従来は追加しかできず、一度登録した語句を画面から消せなかった
        （config.json を手で編集するしかなかった）。
        """
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()},
                      reverse=True)
        if not rows:
            QMessageBox.information(
                self, "削除する行がありません",
                "削除したい行を選んでから「選択行を削除」を押してください。")
            return
        # 後ろの行から削除する（前から消すと以降の行番号がずれる）。
        for r in rows:
            self.table.removeRow(r)

    def _current_words(self) -> list[str]:
        """表に入力されている語句を行順に返す（空欄は除く）。"""
        words = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text():
                words.append(item.text())
        return words

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "辞書CSV", filter="CSV (*.csv)")
        if not path:
            return
        try:
            d, warnings = import_dictionary_csv(Path(path))
        except ValueError as exc:
            QMessageBox.warning(self, "インポートエラー", str(exc))
            return
        # 既存の表と重複する語句は追加しない。追加してしまうと表に同じ語句が
        # 二重に並び、保存時にどちらか一方が黙って捨てられることになる。
        existing = set(self._current_words())
        added, skipped = 0, []
        for word, cat in d.items():
            if word in existing:
                skipped.append(word)
                continue
            self._append_row(word, CATEGORY_LABELS.get(cat, cat))
            existing.add(word)
            added += 1
        messages = [f"{added}件を追加しました。"]
        if skipped:
            messages.append(
                f"既に登録済みのため{len(skipped)}件をスキップしました:\n"
                + "、".join(skipped[:10])
                + ("…" if len(skipped) > 10 else ""))
        messages.extend(warnings)
        QMessageBox.information(self, "インポート結果", "\n\n".join(messages))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "辞書CSV", filter="CSV (*.csv)")
        if path:
            export_dictionary_csv(self._collect_dictionary(), Path(path))

    def _collect_dictionary(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for r in range(self.table.rowCount()):
            word_item = self.table.item(r, 0)
            combo = self.table.cellWidget(r, 1)
            if word_item and word_item.text():
                label = combo.currentText() if combo is not None else "カスタム"
                result[word_item.text()] = LABEL_TO_CATEGORY.get(label, "CUSTOM")
        return result

    def _duplicate_words(self) -> list[str]:
        """表内で2回以上出現する語句を返す（出現順、重複排除済み）。"""
        seen: set[str] = set()
        dups: list[str] = []
        for word in self._current_words():
            if word in seen and word not in dups:
                dups.append(word)
            seen.add(word)
        return dups

    def _select_rows_with(self, words: set[str]) -> None:
        self.table.clearSelection()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() in words:
                self.table.selectRow(r)

    def _on_accept(self) -> None:
        # 同じ語句が複数行にあると、辞書は dict なので後の行が前の行を黙って
        # 上書きする＝登録したはずの種別が消える。保存を止めてユーザーに知らせ、
        # 該当行を選択表示して直せるようにする。
        dups = self._duplicate_words()
        if dups:
            self.tabs.setCurrentIndex(self.TAB_DICTIONARY)  # 辞書タブを表示
            self._select_rows_with(set(dups))
            QMessageBox.warning(
                self, "語句が重複しています",
                "同じ語句が複数の行に登録されています（該当行を選択しました）。\n"
                "重複を削除してから保存してください:\n"
                + "、".join(dups[:10]) + ("…" if len(dups) > 10 else ""))
            return

        # Task 19 調査1: enabled_categories のカテゴリON/OFFフィルタは
        # `core/detector.py` の `Detector.detect()` 側で、
        # source=="dictionary"（カスタム辞書由来）の候補を種別に関わらず
        # 常に素通りさせるよう修正済み（辞書登録＝ユーザーの明示的な
        # 「必ずマスクしたい」という意思表示であり、自動検出(pattern/ner)の
        # ON/OFF設定に巻き込まれて無効化されてはならないため）。そのため
        # ここでは、チェックボックスの状態をそのまま enabled_categories と
        # すればよく、元の参考実装にあった `enabled.add("CUSTOM")`
        # （"CUSTOM"という文字列だけを特別扱いする場当たり的な対処。
        # ユーザーが辞書語にCUSTOM以外の種別、例えばPERSONを割り当てていた
        # 場合は救えなかった）は不要になった。
        enabled = {c for c, cb in self._cat_checks.items() if cb.isChecked()}
        self._config.enabled_categories = enabled
        self._config.custom_dictionary = self._collect_dictionary()
        self.accept()
