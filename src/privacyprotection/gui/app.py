"""アプリのエントリポイント。"""
import sys

from PySide6.QtWidgets import QApplication

# 絶対importを使う理由: このモジュールはPyInstallerビルド時にエントリ
# スクリプト（トップレベル__main__）として直接実行される（scripts/build.ps1）。
# その場合 __package__ が設定されず相対import "from .main_window import ..."
# は "ImportError: attempted relative import with no known parent package"
# で実行時に失敗する（`python -m privacyprotection.gui.app` 実行時は
# __package__ が設定されるため問題にならず、テストでも気付けなかった）。
# 絶対importなら `-m` 実行・フリーズ実行のどちらでも同じように解決する。
from privacyprotection.gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
