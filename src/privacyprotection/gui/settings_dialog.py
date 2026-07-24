"""設定画面（設計書 6.3）。

アーキテクチャ制約（CLAUDE.md）についての注記: `..core.models` から
`CATEGORY_LABELS` をインポートしているが、これは違反ではない
（`gui/preview_dialog.py` と同じ理由）。`CATEGORY_LABELS` は種別コード→
日本語ラベルの固定語彙という素のデータであり、検出器やマスカーの組み立て
（GUI層で禁止されている業務ロジックの再実装）ではない。検出器の組み立ては
これまで通り `services/pipeline.py` の `Pipeline.from_config()` に閉じている。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
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


class SettingsDialog(QDialog):
    """設定画面。OKで渡された `config`（`AppConfig`）を直接更新する。
    呼び出し元（`MainWindow._open_settings`）が `save_config` で永続化する。
    """

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(560, 560)
        self._config = config
        layout = QVBoxLayout(self)

        # 検出カテゴリ ON/OFF
        cat_box = QGroupBox("検出カテゴリ")
        cat_layout = QVBoxLayout(cat_box)
        self._cat_checks: dict[str, QCheckBox] = {}
        for cat in _TOGGLE_CATEGORIES:
            cb = QCheckBox(f"{CATEGORY_LABELS[cat]}（{cat}）")
            cb.setChecked(cat in config.enabled_categories)
            self._cat_checks[cat] = cb
            cat_layout.addWidget(cb)
        layout.addWidget(cat_box)

        # カスタム辞書
        dict_box = QGroupBox("カスタム辞書（語句・種別）")
        dict_layout = QVBoxLayout(dict_box)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["語句", "種別"])
        for word, dict_cat in config.custom_dictionary.items():
            # 手編集のconfig.jsonが固定10種以外のカテゴリを持っていても
            # ここでKeyErrorにせず、コンボボックス側で「カスタム」に
            # フォールバックさせる（Finding 7。フォールバック先の文字列は
            # _CATEGORY_LABEL_CHOICES に存在しないため、_append_row内の
            # findText()が-1を返し、既定の「カスタム」選択になる）。
            self._append_row(word, CATEGORY_LABELS.get(dict_cat, dict_cat))
        dict_layout.addWidget(self.table)
        btn_row = QHBoxLayout()
        for label, fn in [("行を追加", self._add_row),
                          ("CSVインポート", self._import_csv),
                          ("CSVエクスポート", self._export_csv)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btn_row.addWidget(b)
        dict_layout.addLayout(btn_row)
        layout.addWidget(dict_box, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "辞書CSV", filter="CSV (*.csv)")
        if not path:
            return
        try:
            d, warnings = import_dictionary_csv(Path(path))
        except ValueError as exc:
            QMessageBox.warning(self, "インポートエラー", str(exc))
            return
        for word, cat in d.items():
            self._append_row(word, CATEGORY_LABELS[cat])
        if warnings:
            QMessageBox.information(self, "警告", "\n".join(warnings))

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

    def _on_accept(self) -> None:
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
