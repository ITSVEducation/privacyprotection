# PostToolUse hook — core/ または services/ の編集後にプライバシー不変条件を検証する。
#
# CLAUDE.md に文章で書かれている不変条件（重なりはトリムであって破棄ではない／レポートに
# 実値を含めない／Restorer は完全一致のみ 等）は「指示」である以上、長時間セッション・
# 文脈圧縮後・曖昧な要求下では破られうる。ここで決定論的に検証する。
#
# このファイルは UTF-8 BOM 付きで保存すること。Windows PowerShell 5.1 は BOM の無い
# .ps1 を ANSI（CP932）として読むため、BOM を落とすと日本語コメントが壊れて構文エラーになる。
#
# stdin : PostToolUse の JSON ペイロード（UTF-8）
# exit 0: 対象外、または全テスト成功
# exit 2: テスト失敗。stderr が Claude にフィードバックされる
#         （PostToolUse なので編集自体は取り消されない）

$ErrorActionPreference = 'Continue'

# Claude Code は hook の stderr を UTF-8 として読む。PowerShell 5.1 の既定は
# コンソールの OEM コードページ（日本語環境では CP932）なので、明示しないと文字化けする。
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

# 手動実行時に stdin 待ちでハングしないようにする
if (-not [Console]::IsInputRedirected) { exit 0 }

# stdin は UTF-8 で届く。[Console]::In.ReadToEnd() はコンソールの入力コードページで
# 復号するため、日本語を含むペイロードが壊れて ConvertFrom-Json が失敗する
# （実測: PostToolUse の tool_response.originalFile に本リポジトリの日本語コメントが
#  丸ごと入るため、ほぼ毎回壊れていた）。生ストリームを UTF-8 で読み直す。
$reader = New-Object System.IO.StreamReader(
    [Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding $false))
$raw = $reader.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

# PowerShell 5.1 の ConvertFrom-Json は巨大な入力で失敗しうるので、
# 失敗時は生テキストから file_path だけを取り出す経路も用意する。
$filePath = $null
try {
    $filePath = [string]($raw | ConvertFrom-Json).tool_input.file_path
} catch {
    # 破滅的バックトラックを避けるため、否定文字クラスだけの線形パターンにする
    # （ファイルパスに \" が現れることは実質ないので、これで十分）。
    if ($raw -match '"file_path"\s*:\s*"([^"]*)"') {
        $filePath = $Matches[1].Replace('\\', '\')
    }
}
if ([string]::IsNullOrWhiteSpace($filePath)) { exit 0 }

# file_path は絶対・相対どちらもありうるので接尾一致で判定する
$normalized = $filePath -replace '\\', '/'
if ($normalized -notmatch 'src/privacyprotection/(core|services)/[^/]+\.py$') { exit 0 }

# cwd に依存せずリポジトリルートを決める（scripts/hooks/ の2つ上）
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

# venv 未作成のクローン直後などでは黙って通す（毎回エラーを出さない）
if (-not (Test-Path $python)) { exit 0 }

$tests = @(
    'tests/core/test_spans.py'       # 重なり解決はトリムであって破棄ではない
    'tests/core/test_detector.py'    # 3検出器の統合と優先度
    'tests/core/test_masker.py'      # 同一実値→同一トークン／既存トークンとの衝突回避
    'tests/core/test_restorer.py'    # 完全一致のみ。壊れたトークンを推測復元しない
    'tests/core/test_mapping_io.py'  # 対応表の重複トークン拒否
    'tests/services/test_report.py'  # レポートに実値を含めない
    'tests/test_no_network.py'       # 完全ローカル動作
)

# pytest の出力を UTF-8 で受け取る。既定では Python が CP932 で書き出し、
# PowerShell 側は UTF-8 として読むため、テスト名やアサーション内の日本語が化ける。
$env:PYTHONIOENCODING = 'utf-8'

Push-Location $repoRoot
try {
    $output = & $python -m pytest @tests -q --no-header -p no:cacheprovider
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    $tail = ($output | Select-Object -Last 40) -join [Environment]::NewLine
    [Console]::Error.WriteLine("プライバシー不変条件のテストが失敗しました（$normalized の編集後）。")
    [Console]::Error.WriteLine("これは本アプリの存在理由そのものにあたる保証です。テストを弱めるのではなく、編集内容の方を修正してください。")
    [Console]::Error.WriteLine("根拠と検証方法は .claude/rules/core-invariants.md にあります。")
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine($tail)
    exit 2
}

exit 0
