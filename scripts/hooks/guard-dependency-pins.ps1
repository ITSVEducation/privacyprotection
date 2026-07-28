# PreToolUse hook — pyproject.toml の「実測で判明した」依存ピンを外す編集をブロックする。
#
# spacy<3.8 と numpy<2 は、どちらも上限を外すと NER 検出（＝マスク機能全般）が
# 動かなくなることが実測で確認されている。原因が import 時の設定エラー / ABI エラーで
# 「未インストール」と見分けづらいため、事故が起きても気づきにくい。
# CLAUDE.md の「Do not remove this pin」という指示だけに頼らず、ここで機構的に止める。
#
# このファイルは UTF-8 BOM 付きで保存すること。Windows PowerShell 5.1 は BOM の無い
# .ps1 を ANSI（CP932）として読むため、BOM を落とすと日本語コメントが壊れて構文エラーになる。
#
# stdin : PreToolUse の JSON ペイロード（UTF-8）
# exit 0: 対象外、またはピンが維持されている
# exit 2: ブロック（stderr が Claude にエラーとして返る）

$ErrorActionPreference = 'Continue'

# Claude Code は hook の stderr を UTF-8 として読む。PowerShell 5.1 の既定は
# コンソールの OEM コードページ（日本語環境では CP932）なので、明示しないと文字化けする。
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

if (-not [Console]::IsInputRedirected) { exit 0 }

# stdin は UTF-8 で届く。[Console]::In.ReadToEnd() はコンソールの入力コードページで
# 復号するため、pyproject.toml の日本語コメントが old_string / new_string に入ると
# 壊れて ConvertFrom-Json が失敗する。生ストリームを UTF-8 で読み直す。
$reader = New-Object System.IO.StreamReader(
    [Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding $false))
$raw = $reader.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

$guards = @(
    [pscustomobject]@{
        Name     = 'spacy>=3.4.4,<3.8.0'
        Mentions = '(?im)["'']?spacy\s*[><=]'
        Valid    = '(?i)spacy\s*>=\s*3\.4\.4\s*,\s*<\s*3\.8'
        Reason   = 'spacy>=3.8 は ja_ginza の compound_splitter ファクトリを壊す（confection が default_config の split_mode=None を str 型ヒントに対して厳格に拒否する）。ja_ginza 側に修正版は出ていない。'
    },
    [pscustomobject]@{
        Name     = 'numpy<2'
        Mentions = '(?im)["'']?numpy\s*[><=]'
        Valid    = '(?i)numpy\s*<\s*2'
        Reason   = 'numpy 2.x は thinc の C 拡張と ABI 非互換。import spacy が「numpy.dtype size changed」で落ち、NER 検出が全滅する。thinc の依存メタデータには上限が無いため、ここで締め出さないとリゾルバが 2.x を入れてしまう。'
    }
)

function Deny {
    param([object[]]$Violations, [string]$Note)
    [Console]::Error.WriteLine('pyproject.toml の依存バージョンピンを外す編集をブロックしました。')
    [Console]::Error.WriteLine('以下は実測で判明した制約で、外すと NER 検出（＝マスク機能全般）が動かなくなります。')
    foreach ($v in $Violations) {
        [Console]::Error.WriteLine('')
        [Console]::Error.WriteLine(('  - {0}' -f $v.Name))
        [Console]::Error.WriteLine(('    {0}' -f $v.Reason))
    }
    if ($Note) {
        [Console]::Error.WriteLine('')
        [Console]::Error.WriteLine($Note)
    }
    [Console]::Error.WriteLine('')
    [Console]::Error.WriteLine('意図的に上げる場合は、まずユーザーに理由を説明して承認を得てください。')
    [Console]::Error.WriteLine('承認後は spacy.load("ja_ginza") が実際に固有表現（doc.ents）を返すことを')
    [Console]::Error.WriteLine('.venv/Scripts/python -m pytest tests/core/test_ner.py -v で確認してから進めること。')
    [Console]::Error.WriteLine('この hook 自体を編集して迂回しないこと。')
    exit 2
}

$payload = $null
try { $payload = $raw | ConvertFrom-Json } catch { $payload = $null }

if ($null -eq $payload) {
    # パースできないときは判定不能。pyproject.toml が絡むなら fail-closed で止める
    # （黙って通すと、ピンを外す編集が検証されないまま成立してしまう）。
    if ($raw -match 'pyproject\.toml') {
        Deny $guards 'ペイロードを解析できなかったため、ピンが維持されているか検証できませんでした。'
    }
    exit 0
}

$toolInput = $payload.tool_input
if ($null -eq $toolInput) { exit 0 }

$filePath = [string]$toolInput.file_path
if ([string]::IsNullOrWhiteSpace($filePath)) { exit 0 }
if ((Split-Path $filePath -Leaf) -ne 'pyproject.toml') { exit 0 }

# Edit / Write のフィールド名はバージョンによって揺れる（old_string/old_str, content/file_text）
# ので、いずれの綴りも受け付ける。
function Get-Field {
    param($Obj, [string[]]$Names)
    if ($null -eq $Obj) { return $null }
    foreach ($n in $Names) {
        if ($Obj.PSObject.Properties.Name -contains $n) { return [string]$Obj.$n }
    }
    return $null
}

$oldText = ''
$newText = ''
$isFullRewrite = $false

if ($toolInput.PSObject.Properties.Name -contains 'edits') {
    # MultiEdit: 全 edit を連結して判定する
    foreach ($e in $toolInput.edits) {
        $oldText += (Get-Field $e @('old_string', 'old_str')) + "`n"
        $newText += (Get-Field $e @('new_string', 'new_str')) + "`n"
    }
} else {
    $whole = Get-Field $toolInput @('content', 'file_text')
    if ($null -ne $whole) {
        $isFullRewrite = $true
        $newText = $whole
    } else {
        $oldText = [string](Get-Field $toolInput @('old_string', 'old_str'))
        $newText = [string](Get-Field $toolInput @('new_string', 'new_str'))
    }
}

if (-not $isFullRewrite -and [string]::IsNullOrEmpty($oldText) -and [string]::IsNullOrEmpty($newText)) { exit 0 }

$violations = @()
foreach ($g in $guards) {
    $newMentions = $newText -match $g.Mentions
    $newValid = $newText -match $g.Valid

    if ($isFullRewrite) {
        # 全文置換なら、両方のピンが揃っていることを要求する
        if (-not $newValid) { $violations += $g }
        continue
    }

    if ($newMentions -and -not $newValid) { $violations += $g; continue }

    # 該当行そのものを削除しようとしているケース
    if (($oldText -match $g.Mentions) -and -not $newMentions) { $violations += $g }
}

if ($violations.Count -gt 0) { Deny $violations $null }

exit 0
