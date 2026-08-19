"""折りたたみ式の詳細設定パネル（設計書 04 §4.1）。

Qt 標準に disclosure ウィジェットがないため、QToolButton（矢印）＋
コンテナ widget の表示切替で実装する。ヘッダーは畳んだ状態でも現在の
動作を要約表示し、既定（自動判定・可逆・確認あり）から変えている場合は
強調色にして「不可逆のまま気づかない」事故を防ぐ。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QToolButton, QVBoxLayout, QWidget,
)

from ..config import AppConfig

# (表示ラベル, action_mode値) — インデックスが QComboBox の並びと対応する
_ACTION_ITEMS = [("自動判定", "auto"), ("常にマスク", "mask"), ("常に復元", "restore")]
_ACCENT_STYLE = "color: #b06000; font-weight: bold;"


class AdvancedPanel(QWidget):
    settings_requested = Signal()
    dictionary_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.RightArrow)
        self._toggle.setAutoRaise(True)
        self._toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("動作:"))
        self._action_combo = QComboBox()
        for label, _value in _ACTION_ITEMS:
            self._action_combo.addItem(label)
        values = [v for _, v in _ACTION_ITEMS]
        self._action_combo.setCurrentIndex(
            values.index(config.action_mode)
            if config.action_mode in values else 0)
        self._action_combo.currentIndexChanged.connect(self._update_summary)
        action_row.addWidget(self._action_combo)
        action_row.addStretch()
        body_layout.addLayout(action_row)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("方式:"))
        self._token_radio = QRadioButton("可逆（トークン）")
        self._redact_radio = QRadioButton("不可逆（塗りつぶし）")
        # 不正な mask_mode（config.json の手編集・破損・将来の値）は安全側の
        # 可逆に落とす。"redact" と明示されたときだけ不可逆を選ぶ — action_mode
        # を許容値リストで検証して "auto" に落とすのと対称であり、曖昧な設定値
        # のせいで気づかないまま不可逆マスクになる事故を防ぐ。
        (self._redact_radio if config.mask_mode == "redact"
         else self._token_radio).setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self._token_radio)
        group.addButton(self._redact_radio)
        self._token_radio.toggled.connect(self._update_summary)
        method_row.addWidget(self._token_radio)
        method_row.addWidget(self._redact_radio)
        method_row.addStretch()
        body_layout.addLayout(method_row)

        self._skip_preview = QCheckBox("確認なしで即変換")
        self._skip_preview.setChecked(config.skip_preview)
        self._skip_preview.toggled.connect(self._update_summary)
        body_layout.addWidget(self._skip_preview)

        buttons_row = QHBoxLayout()
        self._settings_btn = QPushButton("設定…")
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        self._dictionary_btn = QPushButton("辞書を編集…")
        self._dictionary_btn.setToolTip(
            "必ずマスクしたい社名・製品名・氏名などを登録します")
        self._dictionary_btn.clicked.connect(self.dictionary_requested.emit)
        buttons_row.addWidget(self._settings_btn)
        buttons_row.addWidget(self._dictionary_btn)
        buttons_row.addStretch()
        body_layout.addLayout(buttons_row)

        layout.addWidget(self._body)
        self._toggle.setChecked(config.advanced_expanded)
        self._body.setVisible(config.advanced_expanded)
        self._update_summary()

    # --- 公開アクセサ（MainWindow が config 保存・pipeline 構築に使う） ---
    def action_mode(self) -> str:
        return _ACTION_ITEMS[self._action_combo.currentIndex()][1]

    def mask_mode(self) -> str:
        return "token" if self._token_radio.isChecked() else "redact"

    def skip_preview(self) -> bool:
        return self._skip_preview.isChecked()

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    # --- 内部 -----------------------------------------------------------
    def _on_toggled(self, expanded: bool) -> None:
        self._body.setVisible(expanded)
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def _update_summary(self, *_args) -> None:
        parts = []
        if self.action_mode() == "mask":
            parts.append("常にマスク")
        elif self.action_mode() == "restore":
            parts.append("常に復元")
        parts.append("可逆マスク" if self.mask_mode() == "token" else "不可逆マスク")
        parts.append("即変換" if self.skip_preview() else "確認あり")
        self._toggle.setText(f"詳細設定（{'・'.join(parts)}）")
        is_default = (self.action_mode() == "auto"
                      and self.mask_mode() == "token"
                      and not self.skip_preview())
        self._toggle.setStyleSheet("" if is_default else _ACCENT_STYLE)
