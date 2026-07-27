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
        self.clipboard_btn.clicked.connect(self._clipboard_action)
        layout.addWidget(self.clipboard_btn)

        # 設定・辞書への導線。従来は「ツール」メニューからしか辿れず、
        # カスタム辞書を編集できること自体が見つけにくかったため、メイン画面へ
        # 直接ボタンを置く（メニュー側も従来どおり残す）。
        settings_row = QHBoxLayout()
        self.settings_btn = QPushButton("設定")
        self.settings_btn.clicked.connect(self._open_settings)
        self.dictionary_btn = QPushButton("辞書を編集")
        self.dictionary_btn.setToolTip(
            "必ずマスクしたい社名・製品名・氏名などを登録します")
        self.dictionary_btn.clicked.connect(self._open_dictionary)
        settings_row.addWidget(self.settings_btn)
        settings_row.addWidget(self.dictionary_btn)
        layout.addLayout(settings_row)

        # モード（マスク/復元）に応じてクリップボードボタンのラベルと挙動を
        # 切り替える（Finding 3）。ラジオボタンはどちらか一方のtoggled(True)
        # だけを見れば十分（QButtonGroupで排他制御されているため）。
        self.mask_radio.toggled.connect(self._update_clipboard_button_label)
        self._update_clipboard_button_label()

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
    # `clicked`/`triggered` シグナルは bool(checked) を渡してくるため、スロット
    # 側では受け流す（この bool をタブ番号として受け取ってしまわないよう、
    # タブ指定は _show_settings 側の引数に分けている）。
    def _open_settings(self, *_args):
        from .settings_dialog import SettingsDialog
        self._show_settings(SettingsDialog.TAB_CATEGORIES)

    def _open_dictionary(self, *_args):
        """設定画面を「カスタム辞書」タブを開いた状態で表示する。"""
        from .settings_dialog import SettingsDialog
        self._show_settings(SettingsDialog.TAB_DICTIONARY)

    def _show_settings(self, initial_tab: int):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self, initial_tab=initial_tab)
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
        # --windowed ビルドでは stderr が存在せず、スロット内で送出された
        # 例外は誰にも見えないまま握りつぶされ「操作しても何も起きない」
        # ように見える（spacy_legacy 欠落による RegistryError で実際に
        # 発生した）。個別処理の try/except から漏れる例外（例:
        # _build_pipeline）もここで受け、必ずダイアログとして表示する。
        # メッセージに検出値そのものを含めない制約は例外名のみの表示で守る。
        try:
            if self.restore_radio.isChecked():
                self._restore_paths(paths)
            else:
                self._mask_paths(paths)
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

    def _mask_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        # フォルダ → バックグラウンド一括。即変換ONならプレビューをスキップして
        # そのままマスク。それ以外（単一/複数ファイル＋プレビューあり）は
        # ファイルごとに PreviewDialog を開き、確定後だけマスクを実行する。
        if len(paths) == 1 and paths[0].is_dir():
            self._run_worker(pipeline.mask_folder, paths[0])
        elif self.skip_preview.isChecked():
            for p in paths:
                # 未対応拡張子・(単一ファイルモードへの)フォルダの混在ドロップ・
                # ハンドラの読み書きエラー等、1件の失敗で残りの処理まで
                # 巻き込んで落ちないよう、ファイル単位で例外を捕える
                # （Finding 4）。mask_folder の per-file except ブロックと
                # 同じ一般的なメッセージ形式に揃える。
                try:
                    frags, dets = pipeline.analyze_file(p)
                    out, report = pipeline.mask_file(p, frags, dets)
                    self._show_report_text(report_text=self._single_report(report))
                except Exception as exc:
                    QMessageBox.warning(
                        self, "エラー",
                        f"{p.name}: {type(exc).__name__}: 処理できませんでした")
        else:
            from .preview_dialog import PreviewDialog
            from .report_dialog import ReportDialog
            for p in paths:
                try:
                    frags, dets = pipeline.analyze_file(p)
                    dlg = PreviewDialog(frags, dets, parent=self)
                    if dlg.exec() != PreviewDialog.Accepted:
                        continue
                    out, report = pipeline.mask_file(p, frags, dets)
                    ReportDialog(self._single_report(report), parent=self).exec()
                except Exception as exc:
                    QMessageBox.warning(
                        self, "エラー",
                        f"{p.name}: {type(exc).__name__}: 処理できませんでした")

    def _restore_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        for p in paths:
            try:
                self._restore_one(pipeline, p)
            except Exception as exc:
                # read_mapping の重複トークン検出(ValueError)、未対応拡張子
                # (ValueError)、単一/復元モードへのフォルダの混在ドロップ
                # (ValueError)、Handlerの読み書きエラー等、あらゆる失敗を
                # ここで一括して捕え、ファイル単位のダイアログに留める
                # （Finding 4。mask_folder の except ブロックと同じ文言形式）。
                QMessageBox.warning(
                    self, "復元エラー",
                    f"{p.name}: {type(exc).__name__}: 処理できませんでした")

    def _restore_one(self, pipeline: Pipeline, p: Path) -> None:
        try:
            out, result = pipeline.restore_file(p)
        except FileNotFoundError:
            # 対応表の自動検出（<マスク済ファイル名>.pmap.csv）に失敗した場合
            # （典型的にはフォルダ一括処理の出力: 対応表は共有の
            # `_folder.pmap.csv` にしかない）。ユーザーに対応表を明示的に
            # 指定してもらってから再試行する（Finding 2）。
            mapping_path = self._resolve_mapping_path(p)
            if mapping_path is None:
                return  # ユーザーがキャンセル: このファイルは静かにスキップ
            out, result = pipeline.restore_file(p, mapping_path=mapping_path)
        msg = f"復元しました: {out.name}"
        if result.unknown_tokens:
            msg += f"\n未知トークン {len(result.unknown_tokens)} 件がそのまま残っています"
        QMessageBox.information(self, "復元完了", msg)

    def _resolve_mapping_path(self, masked_path: Path) -> Path | None:
        """対応表(.pmap.csv)の場所をユーザーに教えてもらう（Finding 2）。

        まず同じフォルダに `_folder.pmap.csv`（フォルダ一括処理が書き出す
        共有対応表）がないか一手先に見てみる — フォルダ一括処理の出力を
        復元しようとしている典型ケースをダイアログなしで即座に解決できる。
        見つからなければファイル選択ダイアログでユーザーに指定してもらう。
        """
        folder_mapping = masked_path.parent / "_folder.pmap.csv"
        if folder_mapping.exists():
            return folder_mapping
        path_str, _ = QFileDialog.getOpenFileName(
            self, "対応表(.pmap.csv)を選択", str(masked_path.parent),
            "対応表 (*.pmap.csv)")
        return Path(path_str) if path_str else None

    # --- クリップボード -----------------------------------------------
    def _clipboard_action(self):
        # クリップボードボタンはモード切替ラジオボタンに連動する
        # （Finding 3）: マスクモードでは従来通りマスク、復元モードでは
        # 復元を行う。
        # _handle_paths と同じ理由で、スロット全体を try/except で包み
        # 例外を必ずダイアログとして表示する（_mask_clipboard には
        # 従来 try/except がなく、--windowed ビルドで無言死していた）。
        try:
            if self.restore_radio.isChecked():
                self._restore_clipboard()
            else:
                self._mask_clipboard()
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

    def _update_clipboard_button_label(self):
        if self.restore_radio.isChecked():
            self.clipboard_btn.setText("クリップボードを復元してコピー")
        else:
            self.clipboard_btn.setText("クリップボードをマスクしてコピー")

    def _mask_clipboard(self):
        cb = QGuiApplication.clipboard()
        text = cb.text()
        if not text:
            QMessageBox.information(self, "クリップボード", "テキストがありません")
            return
        pipeline = self._build_pipeline()

        # クリップボードもファイルと同じく、検出結果をプレビューで確認・編集
        # してから確定する（「確認なしで即変換」ONのときはスキップ）。
        # AIへ貼り付ける直前の最終ゲートなので、目視確認できることが望ましい。
        frag, dets = pipeline.analyze_text(text)
        if not self.skip_preview.isChecked():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog([frag], [dets], parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return  # キャンセル時はクリップボードを変更しない
            # PreviewDialog は [dets] を in-place 編集するため、確定後の dets が
            # そのまま編集結果（追加/無効化/カテゴリ変更を反映）になっている。

        pmap = default_config_path().parent / "clipboard.pmap.csv"
        pmap.parent.mkdir(parents=True, exist_ok=True)
        masked, table, count = pipeline.mask_text(
            text, mapping_path=pmap, detections=dets)
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

    def _restore_clipboard(self):
        """クリップボードの文字列（AIの回答を貼り戻したもの等）を、
        指定した対応表で復元する（Finding 3）。ファイルと違い自動検出の
        しようがない生テキストのため、対応表は常にダイアログで指定させる。
        """
        cb = QGuiApplication.clipboard()
        text = cb.text()
        if not text:
            QMessageBox.information(self, "クリップボード", "テキストがありません")
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, "対応表(.pmap.csv)を選択", filter="対応表 (*.pmap.csv)")
        if not path_str:
            return
        pipeline = self._build_pipeline()
        try:
            result = pipeline.restore_text(text, Path(path_str))
        except Exception as exc:
            # 破損・重複トークンの対応表(ValueError)等（Finding 4）。
            QMessageBox.warning(
                self, "復元エラー", f"{type(exc).__name__}: 処理できませんでした")
            return
        cb.setText(result.text)
        if result.unknown_tokens:
            QMessageBox.information(
                self, "復元完了",
                f"クリップボードを復元しました。\n"
                f"未知トークン {len(result.unknown_tokens)} 件がそのまま残っています")
        else:
            QMessageBox.information(self, "復元完了", "クリップボードを復元しました。")

    # --- ワーカー実行 ----------------------------------------------------
    def _run_worker(self, fn, *args):
        # 前回のワーカーがまだ実行中のまま self._worker を差し替えると、
        # 生きているQThreadへの参照を黙って失う（Finding 5）。新規実行を
        # 拒否し、ユーザーに完了を待つよう伝える。
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "処理中", "前の処理が完了するまでお待ちください")
            return
        self.progress.setVisible(True)
        self._set_controls_enabled(False)
        self._worker = MaskWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        """ワーカー実行中は、新たな実行の引き金になりうる操作を止める
        （Finding 5）。"""
        self.drop_zone.setEnabled(enabled)
        self.clipboard_btn.setEnabled(enabled)

    def _on_progress(self, i, total, name):
        self.progress.setMaximum(total)
        self.progress.setValue(i)

    def _on_finished(self, result):
        self.progress.setVisible(False)
        self._set_controls_enabled(True)
        batch, mapping_path = result
        self._show_report_text(batch.render_text())

    def _on_failed(self, message):
        self.progress.setVisible(False)
        self._set_controls_enabled(True)
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
