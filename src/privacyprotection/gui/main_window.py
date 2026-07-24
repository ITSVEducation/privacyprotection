"""メイン画面（設計書 6.1）。

アーキテクチャ制約（CLAUDE.md）: GUI は services/ にのみ依存し、core/・
handlers/ を直接importしない。検出器の組み立て（`Pipeline.from_config`）と
生テキストのマスク（`Pipeline.mask_text`）は、その制約を満たすために
`services/pipeline.py` へ追加した公開APIを介して行う。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ..config import default_config_path, load_config, save_config
from ..services.pipeline import Pipeline
from ..services.report import BatchReport
from .worker import MaskWorker

NOTICE = "自動検出は完全ではありません。重要なデータは必ずプレビューで確認してください"


class DropZone(QLabel):
    def __init__(self, on_paths):
        super().__init__("ここにファイル／フォルダをドロップ\n（またはクリックして選択）")
        self._on_paths = on_paths
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px;"
            " padding: 40px; font-size: 14px; }")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        self._on_paths(paths)

    def mousePressEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(self, "ファイルを選択")
        if files:
            self._on_paths([Path(f) for f in files])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("個人情報マスキング")
        self.resize(560, 480)
        self._config = load_config()
        self._worker: MaskWorker | None = None

        central = QWidget()
        layout = QVBoxLayout(central)

        # モード切替
        mode_row = QHBoxLayout()
        self.mask_radio = QRadioButton("マスク")
        self.restore_radio = QRadioButton("復元")
        self.mask_radio.setChecked(True)
        g1 = QButtonGroup(self)
        g1.addButton(self.mask_radio); g1.addButton(self.restore_radio)
        mode_row.addWidget(self.mask_radio); mode_row.addWidget(self.restore_radio)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 方式選択
        method_row = QHBoxLayout()
        self.token_radio = QRadioButton("可逆（トークン）")
        self.redact_radio = QRadioButton("不可逆（塗りつぶし）")
        (self.token_radio if self._config.mask_mode == "token"
         else self.redact_radio).setChecked(True)
        g2 = QButtonGroup(self)
        g2.addButton(self.token_radio); g2.addButton(self.redact_radio)
        method_row.addWidget(self.token_radio); method_row.addWidget(self.redact_radio)
        method_row.addStretch()
        layout.addLayout(method_row)

        self.skip_preview = QCheckBox("確認なしで即変換")
        self.skip_preview.setChecked(self._config.skip_preview)
        layout.addWidget(self.skip_preview)

        self.drop_zone = DropZone(self._handle_paths)
        layout.addWidget(self.drop_zone, stretch=1)

        self.clipboard_btn = QPushButton("クリップボードをマスクしてコピー")
        self.clipboard_btn.clicked.connect(self._mask_clipboard)
        layout.addWidget(self.clipboard_btn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        notice = QLabel(NOTICE)
        notice.setStyleSheet("color: #b06000;")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self.setCentralWidget(central)

        # メニュー/アクションは self に保持する（ローカル変数のままだと
        # PySide6のオブジェクト所有権管理上、参照が残らずGCされる余地がある
        # ため、他ウィジェットと同様に明示的に self 経由で保持する）。
        self._tools_menu = self.menuBar().addMenu("ツール")
        self._settings_action = self._tools_menu.addAction("設定...")
        self._settings_action.triggered.connect(self._open_settings)

    # --- 設定画面 --------------------------------------------------------
    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec() == SettingsDialog.Accepted:
            save_config(self._config)

    # --- pipeline 構築 -------------------------------------------------
    def _build_pipeline(self) -> Pipeline:
        # mask_mode はラジオボタンの現在の選択を優先する（self._config は
        # 起動時にロードしたスナップショットで、closeEvent で保存するまで
        # 更新されない）。dictionary/enabled_categories 等それ以外の設定は
        # self._config のものをそのまま使う。
        mode = "token" if self.token_radio.isChecked() else "redact"
        cfg = replace(self._config, mask_mode=mode)
        return Pipeline.from_config(cfg)

    # --- 入力処理 -------------------------------------------------------
    def _handle_paths(self, paths: list[Path]):
        if self.restore_radio.isChecked():
            self._restore_paths(paths)
        else:
            self._mask_paths(paths)

    def _mask_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        # フォルダ → バックグラウンド一括。即変換ONならプレビューをスキップして
        # そのままマスク。それ以外（単一/複数ファイル＋プレビューあり）は
        # ファイルごとに PreviewDialog を開き、確定後だけマスクを実行する。
        if len(paths) == 1 and paths[0].is_dir():
            self._run_worker(pipeline.mask_folder, paths[0])
        elif self.skip_preview.isChecked():
            for p in paths:
                frags, dets = pipeline.analyze_file(p)
                out, report = pipeline.mask_file(p, frags, dets)
                self._show_report_text(report_text=self._single_report(report))
        else:
            from .preview_dialog import PreviewDialog
            from .report_dialog import ReportDialog
            for p in paths:
                frags, dets = pipeline.analyze_file(p)
                dlg = PreviewDialog(frags, dets, parent=self)
                if dlg.exec() != PreviewDialog.Accepted:
                    continue
                out, report = pipeline.mask_file(p, frags, dets)
                ReportDialog(self._single_report(report), parent=self).exec()

    def _restore_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        for p in paths:
            try:
                out, result = pipeline.restore_file(p)
                msg = f"復元しました: {out.name}"
                if result.unknown_tokens:
                    msg += f"\n未知トークン {len(result.unknown_tokens)} 件がそのまま残っています"
                QMessageBox.information(self, "復元完了", msg)
            except FileNotFoundError as exc:
                QMessageBox.warning(self, "復元エラー", str(exc))

    def _mask_clipboard(self):
        cb = QGuiApplication.clipboard()
        text = cb.text()
        if not text:
            QMessageBox.information(self, "クリップボード", "テキストがありません")
            return
        pipeline = self._build_pipeline()
        pmap = default_config_path().parent / "clipboard.pmap.csv"
        pmap.parent.mkdir(parents=True, exist_ok=True)
        masked, table, count = pipeline.mask_text(text, mapping_path=pmap)
        cb.setText(masked)
        if count == 0:
            # 検出0件の理由は「トークンモードで対応表が空」ではなく
            # detector自体が何も検出しなかったこと。redactモードでは
            # Masker の仕様上 table.entries は常に空になるため、
            # table.entries の有無だけで判定すると不可逆モードで
            # 「検出できているのに検出0件」と誤表示してしまう
            # （元プランの参考実装にあったバグ）。
            QMessageBox.information(self, "マスク完了",
                                    "検出0件です（検出漏れの可能性があります）")
        elif table.entries:
            QMessageBox.information(
                self, "マスク完了",
                f"{count}件をマスクしてコピーしました。\n対応表: {pmap}")
        else:
            QMessageBox.information(self, "マスク完了",
                                    f"{count}件をマスクしてコピーしました。")

    # --- ワーカー実行 ----------------------------------------------------
    def _run_worker(self, fn, *args):
        self.progress.setVisible(True)
        self._worker = MaskWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, i, total, name):
        self.progress.setMaximum(total)
        self.progress.setValue(i)

    def _on_finished(self, result):
        self.progress.setVisible(False)
        batch, mapping_path = result
        self._show_report_text(batch.render_text())

    def _on_failed(self, message):
        self.progress.setVisible(False)
        QMessageBox.critical(self, "エラー", message)

    def _show_report_text(self, report_text: str):
        from .report_dialog import ReportDialog
        ReportDialog(report_text, parent=self).exec()

    @staticmethod
    def _single_report(report) -> str:
        return BatchReport(files=[report]).render_text()

    def closeEvent(self, e):
        self._config.skip_preview = self.skip_preview.isChecked()
        self._config.mask_mode = "token" if self.token_radio.isChecked() else "redact"
        save_config(self._config)
        super().closeEvent(e)
