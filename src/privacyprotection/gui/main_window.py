"""メイン画面（設計書 6.1）。

アーキテクチャ制約（CLAUDE.md）: GUI は services/ にのみ依存し、core/・
handlers/ を直接importしない。検出器の組み立て（`Pipeline.from_config`）と
生テキストのマスク（`Pipeline.mask_text`）は、その制約を満たすために
`services/pipeline.py` へ追加した公開APIを介して行う。意図（マスク/復元）の
自動判定は `services/intent.py` に集約し、ここでは判定結果に従って
確認ダイアログを挟むかどうかだけを決める。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from ..config import (
    default_config_path, load_config, merge_new_dictionary_entries,
    save_config,
)
from ..services.intent import (
    FOLDER_MAPPING_NAME, Intent, decide_file_intent, decide_folder_intent,
    decide_text_intent, needs_confirmation,
)
from ..services.pipeline import Pipeline
from ..services.report import BatchReport
from .advanced_panel import AdvancedPanel
from .worker import MaskWorker

NOTICE = "自動検出は完全ではありません。重要なデータは必ずプレビューで確認してください"


class DropZone(QLabel):
    def __init__(self, on_paths):
        super().__init__("ここにファイル／フォルダをドロップ\n"
                         "（クリックで選択 ／ Ctrl+V でクリップボードを処理）")
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
        self._on_done: Callable[[object], None] | None = None

        central = QWidget()
        layout = QVBoxLayout(central)

        self.drop_zone = DropZone(self._handle_paths)
        layout.addWidget(self.drop_zone, stretch=1)

        self.clipboard_btn = QPushButton("クリップボードを処理してコピー")
        self.clipboard_btn.clicked.connect(self._clipboard_action)
        layout.addWidget(self.clipboard_btn)

        self.advanced_panel = AdvancedPanel(self._config)
        self.advanced_panel.settings_requested.connect(self._open_settings)
        self.advanced_panel.dictionary_requested.connect(self._open_dictionary)
        layout.addWidget(self.advanced_panel)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        notice = QLabel(NOTICE)
        notice.setStyleSheet("color: #b06000;")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self.setCentralWidget(central)

        # Ctrl+V はクリップボードボタンと同じ処理を起動する（設計書 04 §4.1）。
        # 参照を self に保持するのはメニュー/アクションと同じ理由（GC対策）。
        self._paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        self._paste_shortcut.activated.connect(self._clipboard_action)

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
        # mask_mode はパネルの現在の選択を優先する（self._config は起動時の
        # スナップショットで、closeEvent で保存するまで更新されない）。
        cfg = replace(self._config, mask_mode=self.advanced_panel.mask_mode())
        return Pipeline.from_config(cfg)

    # --- 復元確認 --------------------------------------------------------
    def _confirm_restore(self, subject: str) -> bool:
        """復元と自動判定したときの確認。いいえ→マスクにフォールバック。"""
        return QMessageBox.question(
            self, "復元の確認",
            f"マスク済み{subject}のようです。復元しますか？\n"
            "「いいえ」を選ぶとマスクします。",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes

    def _wants_restore(self, intent: Intent, action_mode: str, subject: str) -> bool:
        """復元経路に進むか。判定規則そのものは services/intent.py が持ち、
        ここはダイアログの提示だけを受け持つ。確認で「いいえ」を選んだ場合は
        マスクにフォールバックする。
        """
        if intent is not Intent.RESTORE:
            return False
        if not needs_confirmation(intent, action_mode):
            return True
        return self._confirm_restore(subject)

    # --- 入力処理 -------------------------------------------------------
    def _handle_paths(self, paths: list[Path]):
        # --windowed ビルドでは stderr が存在せず、スロット内で送出された
        # 例外は誰にも見えないまま握りつぶされ「操作しても何も起きない」
        # ように見える（spacy_legacy 欠落による RegistryError で実際に
        # 発生した）。個別処理の try/except から漏れる例外（例:
        # _build_pipeline）もここで受け、必ずダイアログとして表示する。
        # メッセージに検出値そのものを含めない制約は例外名のみの表示で守る。
        try:
            self._dispatch_paths(paths)
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

    def _dispatch_paths(self, paths: list[Path]):
        pipeline = self._build_pipeline()
        action_mode = self.advanced_panel.action_mode()

        if len(paths) == 1 and paths[0].is_dir():
            root = paths[0]
            intent = decide_folder_intent(root, action_mode)
            if self._wants_restore(intent, action_mode, "フォルダ"):
                self._run_worker(pipeline.restore_folder, root,
                                 on_done=self._on_folder_restored)
            else:
                self._run_worker(pipeline.mask_folder, root,
                                 on_done=self._on_folder_masked)
            return

        for p in paths:
            # 未対応拡張子・単一ファイルモードへのフォルダ混在ドロップ・
            # ハンドラの読み書きエラー・read_mapping の重複トークン検出など、
            # あらゆる失敗を1ファイルごとに捕え、1件の失敗で残りの処理まで
            # 巻き込んで落ちないよう、ファイル単位のダイアログに留める。
            # マスク・復元の両経路がこの単一の except ブロックを共有する
            # （Finding 4）。mask_folder の per-file except ブロックと同じ
            # 一般的なメッセージ形式に揃える。
            try:
                fragments = pipeline.read_fragments(p)
                intent = decide_file_intent(
                    p, [f.text for f in fragments], action_mode)
                if self._wants_restore(intent, action_mode, "ファイル"):
                    self._restore_one(pipeline, p)
                else:
                    self._mask_one(pipeline, p, fragments)
            except Exception as exc:
                QMessageBox.warning(
                    self, "エラー",
                    f"{p.name}: {type(exc).__name__}: 処理できませんでした")

    def _mask_one(self, pipeline: Pipeline, p: Path, fragments):
        frags, dets = pipeline.analyze_file(p, fragments=fragments)
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog(frags, dets, parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return
            self._register_manual_entries(dlg.accepted_manual_entries())
        _, report = pipeline.mask_file(p, frags, dets)
        self._show_report_text(self._single_report(report))

    def _register_manual_entries(self, entries: dict[str, str]) -> None:
        """プレビューで手動追加された語句をカスタム辞書へ自動登録する。

        既存語句は上書きしない（設定画面で意図して割り当てた種別を守る）。
        通知はしない: 登録内容は設定画面のカスタム辞書タブでいつでも確認・
        削除できる。closeEvent 任せにせず即保存する（異常終了でも残るように）。
        """
        added = merge_new_dictionary_entries(
            self._config.custom_dictionary, entries)
        if added:
            self._config.custom_dictionary.update(added)
            save_config(self._config)

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
        folder_mapping = masked_path.parent / FOLDER_MAPPING_NAME
        if folder_mapping.exists():
            return folder_mapping
        path_str, _ = QFileDialog.getOpenFileName(
            self, "対応表(.pmap.csv)を選択", str(masked_path.parent),
            "対応表 (*.pmap.csv)")
        return Path(path_str) if path_str else None

    # --- クリップボード -----------------------------------------------
    def _clipboard_action(self):
        # _handle_paths と同じ理由で、スロット全体を try/except で包み
        # 例外を必ずダイアログとして表示する（_mask_clipboard には
        # 従来 try/except がなく、--windowed ビルドで無言死していた）。
        try:
            text = QGuiApplication.clipboard().text()
            if not text:
                QMessageBox.information(self, "クリップボード", "テキストがありません")
                return
            action_mode = self.advanced_panel.action_mode()
            intent = decide_text_intent(text, action_mode)
            if self._wants_restore(intent, action_mode, "テキスト"):
                self._restore_clipboard()
            else:
                self._mask_clipboard()
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")

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
        if not self.advanced_panel.skip_preview():
            from .preview_dialog import PreviewDialog
            dlg = PreviewDialog([frag], [dets], parent=self)
            if dlg.exec() != PreviewDialog.Accepted:
                return  # キャンセル時はクリップボードを変更しない
            # PreviewDialog は [dets] を in-place 編集するため、確定後の dets が
            # そのまま編集結果（追加/無効化/カテゴリ変更を反映）になっている。
            self._register_manual_entries(dlg.accepted_manual_entries())

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
    def _run_worker(self, fn, *args, on_done):
        # 前回のワーカーがまだ実行中のまま self._worker を差し替えると、
        # 生きているQThreadへの参照を黙って失う（Finding 5）。新規実行を
        # 拒否し、ユーザーに完了を待つよう伝える。
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "処理中", "前の処理が完了するまでお待ちください")
            return
        self.progress.setVisible(True)
        self._set_controls_enabled(False)
        self._on_done = on_done
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
        self.advanced_panel.setEnabled(enabled)
        self._paste_shortcut.setEnabled(enabled)

    def _on_progress(self, i, total, name):
        self.progress.setMaximum(total)
        self.progress.setValue(i)

    def _on_finished(self, result):
        # _handle_paths と同じ理由でこのスロット全体を保護する: _on_done
        # (_on_folder_masked/_on_folder_restored) は batch.render_text() や
        # ReportDialog の構築で例外を送出し得るが、ここはQtのシグナル
        # ハンドラなので、包まずに漏らすと --windowed ビルドでは誰にも
        # 見えないまま握りつぶされる。進捗バーを隠す・操作を再度有効化する
        # 後始末は、_on_done が例外を投げたかどうかに関わらず必ず行う。
        try:
            self._on_done(result)
        except Exception as exc:
            QMessageBox.critical(
                self, "エラー", f"{type(exc).__name__}: 処理できませんでした")
        finally:
            self.progress.setVisible(False)
            self._set_controls_enabled(True)

    def _on_folder_masked(self, result):
        batch, _ = result
        self._show_report_text(batch.render_text())

    def _on_folder_restored(self, result):
        restored, warnings = result
        msg = f"{restored} 件のファイルを復元しました。"
        if warnings:
            msg += "\n\n" + "\n".join(warnings)
        QMessageBox.information(self, "復元完了", msg)

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
        self._config.skip_preview = self.advanced_panel.skip_preview()
        self._config.mask_mode = self.advanced_panel.mask_mode()
        self._config.action_mode = self.advanced_panel.action_mode()
        self._config.advanced_expanded = self.advanced_panel.is_expanded()
        save_config(self._config)
        super().closeEvent(e)
