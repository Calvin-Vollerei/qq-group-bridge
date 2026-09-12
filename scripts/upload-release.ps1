# upload-release.ps1  -- minimal curl uploader
# Upload dist\qgb-v1.0-*.zip to a GitHub Release.
#
# WHY THIS SCRIPT LOOKS "STUPID" (every curl call written out by hand):
#   Windows PowerShell mangles three things, all verified in practice:
#     * -w "`nHTTP:%{http_code}"  -> the backtick-n is passed literally to curl,
#       which answers: curl: (43) A libcurl function was given a bad argument
#     * -o <temp path with spaces> -> the path gets split into several arguments
#       -> same curl (43)
#     * building an argument array (@common) and splatting it -> same (43)
#   So: no -w, no -o, no argument arrays. Success is judged by curl's EXIT CODE
#   (0 = request completed and HTTP status was < 400), and the response body is
#   printed on failure so the real reason is always visible.
#
# Usage:
#   cd C:\Users\oxyge\Downloads\qq-group-bridge
#   $env:GH_TOKEN = "your token"   # needs Contents: Read and write (or classic repo)
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\upload-release.ps1
#
#   Add -OnlySmall to upload just the 22 MB package.

param(
    [string]$Repo = 'Calvin-Vollerei/qq-group-bridge',
    [string]$Tag  = 'v1.0',
    [string]$Dist = 'dist',
    [switch]$OnlySmall
)

$ErrorActionPreference = 'Continue'

$curl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not (Test-Path $curl)) { $curl = 'curl.exe' }

$token = $env:GH_TOKEN
if (-not $token) { Write-Host 'FAIL: $env:GH_TOKEN is not set' -ForegroundColor Red; exit 1 }

$kind = 'unknown'
if ($token -match '^github_pat_') { $kind = 'fine-grained' } elseif ($token -match '^ghp_') { $kind = 'classic' }

Write-Host "curl     : $curl"
Write-Host "token    : length $($token.Length), type $kind"
Write-Host ""

$auth = "Authorization: Bearer $token"

# ---------------------------------------------------------------- 1. release
Write-Host "Querying release $Tag ..."
$raw  = & $curl -s -H $auth -H "Accept: application/vnd.github+json" -H "User-Agent: qgb" "https://api.github.com/repos/$Repo/releases/tags/$Tag"
$exit = $LASTEXITCODE
$text = ($raw | Out-String)
Write-Host "  curl exit code: $exit"

if ($exit -ne 0) {
    Write-Host "  FAIL: curl could not complete. Output:" -ForegroundColor Red
    Write-Host "  $($text.Trim())"
    exit 1
}

$release = $null
try { $release = $text | ConvertFrom-Json } catch {}

if ($release -and $release.id) {
    Write-Host "  OK: release exists: $($release.name)  id=$($release.id)" -ForegroundColor Green
} else {
    Write-Host "  no release for $Tag, creating ..."
    $bodyFile = Join-Path $env:TEMP 'qgb-release.json'
    $json = '{"tag_name":"' + $Tag + '","name":"' + $Tag + '","draft":false,"prerelease":false,"body":"See repo README: download, then three steps. Windows 10/11 x64."}'
    [System.IO.File]::WriteAllText($bodyFile, $json, (New-Object System.Text.UTF8Encoding($false)))

    $raw  = & $curl -s -X POST -H $auth -H "User-Agent: qgb" -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" --data-binary "@$bodyFile" "https://api.github.com/repos/$Repo/releases"
    $exit = $LASTEXITCODE
    $text = ($raw | Out-String)
    $release = $null
    try { $release = $text | ConvertFrom-Json } catch {}

    if ($exit -ne 0 -or -not $release.id) {
        Write-Host "  FAIL: could not create release (curl exit $exit)" -ForegroundColor Red
        Write-Host "  $($text.Trim())"
        exit 1
    }
    Write-Host "  OK: created $($release.html_url)" -ForegroundColor Green
}

# ---------------------------------------------------------------- 2. upload
$files = @(Get-ChildItem $Dist -File -Filter 'qgb-v1.0-*.zip' | Sort-Object Length)
if ($OnlySmall) { $files = @($files | Where-Object { $_.Name -like '*app-only*' }) }
if (-not $files) { Write-Host "FAIL: no qgb-v1.0-*.zip under $Dist" -ForegroundColor Red; exit 1 }

$base = "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets"
$failed = @()

foreach ($f in $files) {
    Write-Host ""
    Write-Host ("Uploading {0} ({1:N2} MB) ..." -f $f.Name, ($f.Length / 1MB))
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $raw  = & $curl -s -X POST -H $auth -H "User-Agent: qgb" -H "Content-Type: application/zip" --data-binary "@$($f.FullName)" "${base}?name=$($f.Name)"
    $exit = $LASTEXITCODE
    $sw.Stop()
    $text = ($raw | Out-String)

    Write-Host ("  curl exit {0}, elapsed {1:N0}s" -f $exit, $sw.Elapsed.TotalSeconds)

    $resp = $null
    try { $resp = $text | ConvertFrom-Json } catch {}

    if ($exit -eq 0 -and $resp -and $resp.state -eq 'uploaded') {
        Write-Host "  OK: $($resp.name)  $([math]::Round($resp.size/1MB,2)) MB" -ForegroundColor Green
        Write-Host "      $($resp.browser_download_url)"
    } else {
        Write-Host "  FAIL" -ForegroundColor Red
        if ($text.Trim()) { Write-Host "      server: $($text.Trim())" }
        if ($exit -eq 43) { Write-Host "      curl 43 = bad argument (should not happen now; please report this line)" -ForegroundColor Yellow }
        if ($exit -eq 55) { Write-Host "      curl 55 = send failure (unstable link for large files)" -ForegroundColor Yellow }
        if ($exit -eq 56) { Write-Host "      curl 56 = long connection cut" -ForegroundColor Yellow }
        if ($text -match 'Resource not accessible') { Write-Host "      token lacks Contents: write" -ForegroundColor Yellow }
        if ($text -match 'already_exists') { Write-Host "      asset with the same name already exists; delete it on the release page first" -ForegroundColor Yellow }
        $failed += $f.Name
    }
}

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "ALL DONE: $($release.html_url)" -ForegroundColor Green
} else {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Yellow
    Write-Host "Tip: run again with -OnlySmall to publish just the 22 MB package." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Remember to delete the token at https://github.com/settings/personal-access-tokens" -ForegroundColor Yellow
