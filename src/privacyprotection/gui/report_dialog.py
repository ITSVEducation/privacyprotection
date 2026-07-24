"""処理後レポート表示（設計書 6.4）。

呼び出し側は `BatchReport.render_text()`（`services/report.py`、Task 14）が
返すテキストのみを渡すこと。そのテキストには検出された値そのものは一切
含まれない（件数・種別・ファイル名のみ）—このダイアログ自身も、それ以外の
入力を受け取ってPIIの値を画面に出さないよう、渡されたテキストをそのまま
表示するだけに徹する。
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


class ReportDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("処理レポート")
        self.resize(640, 480)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        layout.addWidget(buttons)
