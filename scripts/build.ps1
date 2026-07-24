# scripts/build.ps1
# PyInstaller one-folder ビルド（設計書 9.2）。リポジトリルートで実行する。
$ErrorActionPreference = "Stop"

# spaCy/GiNZAモデルを同梱するため collect-all を使う
.venv\Scripts\pyinstaller `
    --noconfirm --windowed --name PrivacyProtection `
    --collect-all ja_ginza `
    --collect-all ginza `
    --collect-all spacy `
    --collect-all sudachipy `
    --collect-all sudachidict_core `
    src/privacyprotection/gui/app.py

# pyinstaller はネイティブコマンドのため、失敗時も既定では例外を送出しない。
# 終了コードを明示的に確認し、失敗時は後続のZip化・ハッシュ生成に進まないようにする。
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

# 配布ZIPとSHA-256（設計書 9.2）
Compress-Archive -Force -Path dist\PrivacyProtection -DestinationPath dist\PrivacyProtection.zip
(Get-FileHash dist\PrivacyProtection.zip -Algorithm SHA256).Hash |
    Out-File -Encoding ascii dist\PrivacyProtection.zip.sha256
Write-Host "Build complete: dist\PrivacyProtection.zip"
