#Requires -Version 5.1
<#
.SYNOPSIS
    QQ群文件搬运工 —— 一键打包脚本

.DESCRIPTION
    1) 前置校验：语法、单元测试、离线冒烟、GUI 结构、发布扫密（任一失败即中止）
    2) PyInstaller 构建（onedir 文件夹模式）
    3) 组装绿色发布目录：启动/自检脚本、便携标记、使用说明
    4) 对**构建产物本身**跑一次自检，确认打出来的包能跑

.PARAMETER SkipTests
    跳过前置测试。仅用于本地快速迭代，正式发布请勿使用。

.PARAMETER NoClean
    不清理 build/dist，用于增量构建加速。

.EXAMPLE
    pwsh -File scripts/build.ps1
    pwsh -File scripts/build.ps1 -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$NoClean
)

# 只有 cmdlet 错误才终止；原生命令的 stderr 交给 Invoke-Native 处理
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$Root         = Split-Path -Parent $PSScriptRoot
$DistDir      = Join-Path $Root 'dist'
$BuildDir     = Join-Path $Root 'build'
$AppName      = 'QQGroupBridge'
$ReleaseName  = 'QQ群文件搬运工'
$ReleaseDir   = Join-Path $DistDir $ReleaseName
$PackagingDir = Join-Path $Root 'packaging'

Set-Location $Root

# ---------------------------------------------------------------- 输出助手

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
}
function Write-Ok([string]$Text)    { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "  [!!] $Text" -ForegroundColor Yellow }
function Fail([string]$Text)        { Write-Host ''; Write-Host "  [FAIL] $Text" -ForegroundColor Red; exit 1 }

<#
    运行原生命令。

    为什么不用简单的 `& python ...`：PowerShell 会把原生命令写到 stderr 的
    内容包装成 ErrorRecord，在 $ErrorActionPreference='Stop' 下会**直接终止
    脚本** —— 测试输出里的一行日志就足以让打包失败。这里用 `*>` 把所有输出流
    重定向到临时文件，只看 $LASTEXITCODE，彻底避开这个坑。
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$What,
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [int]$Tail = 6
    )
    $log = Join-Path $env:TEMP ("qgb-build-" + [guid]::NewGuid().ToString('N') + ".log")
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $code = 0
    try {
        & $Exe @Arguments *> $log
        $code = $LASTEXITCODE
    } catch {
        Write-Host "      $_" -ForegroundColor DarkGray
        $code = 1
    } finally {
        $ErrorActionPreference = $previous
    }

    if (Test-Path $log) {
        $lines = Get-Content $log -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($lines) {
            $lines | Select-Object -Last $Tail | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        }
        Remove-Item $log -Force -ErrorAction SilentlyContinue
    }

    if ($code -ne 0) { Fail "$What 失败（退出码 $code）" }
}

# ---------------------------------------------------------------- 1 环境

Write-Step '1/6  环境检查'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail '未找到 python，请先安装 Python 3.10+ 并加入 PATH'
}

Invoke-Native -What 'Python 版本检查' -Exe 'python' -Arguments @('--version') -Tail 2
Invoke-Native -What 'PyInstaller 检查' -Exe 'python' -Arguments @(
    '-c', 'import PyInstaller; print("PyInstaller", PyInstaller.__version__)'
) -Tail 2

if (-not (Test-Path (Join-Path $Root "$AppName.spec"))) { Fail "缺少 $AppName.spec" }
if (-not (Test-Path $PackagingDir)) { Fail '缺少 packaging 目录' }
Write-Ok '环境就绪'

# ---------------------------------------------------------------- 2 前置校验

if (-not $SkipTests) {
    Write-Step '2/6  前置校验（任一失败即中止发布）'

    Write-Host '  · 语法检查…'
    Invoke-Native -What '语法检查' -Exe 'python' -Arguments @(
        '-m', 'compileall', '-q', 'qgb', 'tests', 'run_bridge.py', 'scripts'
    ) -Tail 3
    Write-Ok '语法检查通过'

    Write-Host '  · 单元测试…'
    Invoke-Native -What '单元测试' -Exe 'python' -Arguments @(
        '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'
    ) -Tail 4
    Write-Ok '单元测试通过'

    Write-Host '  · 离线端到端冒烟…'
    Invoke-Native -What '离线冒烟' -Exe 'python' -Arguments @('-m', 'qgb.dev.smoke') -Tail 5
    Write-Ok '离线冒烟通过'

    Write-Host '  · GUI 结构检查…'
    Invoke-Native -What 'GUI 结构检查' -Exe 'python' -Arguments @('scripts/gui_inspect.py') -Tail 3
    Write-Ok 'GUI 结构检查通过'

    Write-Host '  · 发布扫密（防止把凭据打进分发包）…'
    Invoke-Native -What '发布扫密' -Exe 'python' -Arguments @('scripts/scan_secrets.py', '--source', '--quiet') -Tail 4
    Write-Ok '扫密通过：源码中未发现敏感信息（构建产物在第 6 步单独扫描）'

    Write-Ok '前置校验全部通过'
} else {
    Write-Step '2/6  前置校验（已跳过）'
    Write-Warn2 '跳过了测试与扫密 —— 仅限调试，正式发布请勿使用'
}

# ---------------------------------------------------------------- 3 清理
#
# ⚠️ 这里**只清理构建产物**，绝不碰 dist\<发布目录>\data\。
#    data\ 是「用户的部署成果」：NapCat 安装、配置、DPAPI 凭据、状态库。
#    而且 NapCat 的工作目录就在 data\napcat\shell\ —— 进程运行时该目录
#    被占用，连移动都会失败（实测：Move-Item 报 Access is denied）。
#    所以组装阶段改成「只替换程序本体」，data\ 原地不动。

Write-Step '3/6  清理旧产物'
if (-not $NoClean) {
    foreach ($dir in @($BuildDir, (Join-Path $DistDir $AppName))) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
}
New-Item -ItemType Directory -Force $DistDir | Out-Null

if (Test-Path (Join-Path $ReleaseDir 'data')) {
    Write-Warn2 '保留既有 data\（NapCat、配置、凭据不会被动到）'
}
Write-Ok '已清理（-NoClean 可跳过）'

# ---------------------------------------------------------------- 4 构建

Write-Step '4/6  PyInstaller 构建（onedir，窗口模式）'
Invoke-Native -What 'PyInstaller 构建' -Exe 'python' -Arguments @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', "$AppName.spec"
) -Tail 8

$builtDir = Join-Path $DistDir $AppName
$builtExe = Join-Path $builtDir "$AppName.exe"
if (-not (Test-Path $builtExe)) { Fail "构建产物缺失：$builtExe" }
Write-Ok "构建完成：$builtDir"

# ---------------------------------------------------------------- 5 组装

Write-Step '5/6  组装绿色发布目录'

# 只替换**程序本体**（exe 与 _internal），data\ 原地保留。
# 理由见第 3 步：data\ 属于运行期数据，且运行中的 NapCat 会占用它。
#
# 组装用**覆盖式**（复制内容），而不是「先删后移」：
#   实测遇到过单个文件（_internal\VCRUNTIME140.dll）被杀软或残留句柄占用，
#   删不掉就让整个打包失败，而且失败前 exe 已经被删掉了 —— 发布目录直接残缺。
#   覆盖式更新对占用文件是"跳过"，其余文件照常更新，打包不会半途而废。
New-Item -ItemType Directory -Force $ReleaseDir | Out-Null

$skipped = @()
foreach ($item in Get-ChildItem -Path $builtDir) {
    Copy-Item -Path $item.FullName -Destination $ReleaseDir -Recurse -Force -ErrorAction SilentlyContinue
    if ($item.PSIsContainer) {
        # 校验：目录里的文件是否都到位（漏掉的通常就是被占用的那几个）
        $srcCount = (Get-ChildItem $item.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        $dstDir = Join-Path $ReleaseDir $item.Name
        $dstCount = (Get-ChildItem $dstDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($dstCount -lt $srcCount) {
            $skipped += "$($item.Name)（$($srcCount - $dstCount) 个文件未更新）"
        }
    }
}

# 抽查关键文件确实落地了（**必须在删掉构建产物之前**检查）
$exePath = Join-Path $ReleaseDir "$AppName.exe"
if (-not (Test-Path $exePath)) { Fail "组装后缺少 $AppName.exe" }

Remove-Item -Recurse -Force $builtDir -ErrorAction SilentlyContinue

if ($skipped.Count -gt 0) {
    Write-Warn2 ("有文件被占用未更新：{0}" -f ($skipped -join '；'))
    Write-Warn2 '通常是杀毒软件正在扫描，关闭后重新打包即可；本次产物一般仍可运行'
}
Write-Ok "程序本体已更新到「$ReleaseName」（data\ 未改动）"

# 便携模式标记：数据目录跟着程序走，不散落在系统盘
New-Item -ItemType File -Force -Path (Join-Path $ReleaseDir 'portable.marker') | Out-Null
Write-Ok 'portable.marker 已写入（数据保存在程序目录下的 data\）'

# 启动与自检脚本（.bat 保持纯 ASCII，避免命令行编码问题）
foreach ($name in @('启动搬运工.bat', '自检.bat', '以管理员身份启动.bat')) {
    $src = Join-Path $PackagingDir $name
    if (-not (Test-Path $src)) { Fail "缺少 $src" }
    Copy-Item $src (Join-Path $ReleaseDir $name) -Force
}
Write-Ok '启动脚本、自检脚本、管理员启动脚本已就位'

# 使用说明：转成「UTF-8 带 BOM」，保证记事本打开不乱码
$readmeSrc = Join-Path $PackagingDir '使用说明.txt'
if (Test-Path $readmeSrc) {
    $content = Get-Content -Raw -Encoding UTF8 $readmeSrc
    $utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText((Join-Path $ReleaseDir '使用说明.txt'), $content, $utf8Bom)
    Write-Ok '使用说明.txt 已就位（UTF-8 BOM，记事本可读）'
}

# 技术文档一并带上，便于后续维护
foreach ($doc in @('README.md', 'requirements.txt')) {
    $src = Join-Path $Root $doc
    if (Test-Path $src) { Copy-Item $src (Join-Path $ReleaseDir $doc) -Force }
}
Write-Ok 'README 与依赖清单已复制'

# ---------------------------------------------------------------- 6 验证

Write-Step '6/6  验证构建产物'

$exe = Join-Path $ReleaseDir "$AppName.exe"
$report = Join-Path $env:TEMP 'qgb-build-selftest.txt'
if (Test-Path $report) { Remove-Item -Force $report }

# 窗口模式的可执行文件不会把 stdout 接到控制台，因此必须让它把报告写到文件
$proc = Start-Process -FilePath $exe `
    -ArgumentList @('--selftest', '--out', $report) `
    -Wait -PassThru -WindowStyle Hidden

if (Test-Path $report) {
    Write-Host ''
    Get-Content $report -Encoding UTF8 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    Write-Host ''
}

if ($proc.ExitCode -ne 0) { Fail "构建产物自检失败（退出码 $($proc.ExitCode)）" }
Write-Ok '构建产物自检通过：依赖与加密链路正常'

# 光有自检不够 —— 自检只证明"模块都在"，还要证明"窗口画得出来"
Write-Host '  · 窗口渲染验证…'
Invoke-Native -What '窗口渲染验证' -Exe 'python' -Arguments @(
    'scripts/verify_package.py', '--exe', $exe
) -Tail 8
Write-Ok '窗口渲染验证通过：打包后的程序能启动并画出界面'

# 便携模式：确认数据目录落在发布目录内
$dataDir = Join-Path $ReleaseDir 'data'
if (Test-Path $dataDir) {
    Write-Ok '便携数据目录已创建：data\（程序自带，不写系统盘）'
} else {
    Write-Warn2 '未在发布目录看到 data\ —— 请确认 portable.marker 随包分发'
}

# ---------------------------------------------------------------- 6 分发包
#
# ⚠️ 这一节是**安全闸门**，不是可选项。
#    发布目录里的 data\ 是运行期数据（NapCat、用户配置、DPAPI 凭据、状态库）。
#    把整个文件夹压成 zip 发给朋友，就等于把自己的账号状态一起发了出去。
#    所以分发包必须按白名单单独生成，并扫描「将要发出去的那个 zip」。

Write-Step '6/6  生成可分发包并扫描'

Invoke-Native -What '生成可分发包' -Exe 'python' -Arguments @(
    'scripts/make_release_zip.py', '--release-dir', $ReleaseDir
) -Tail 12
$DistZip = Join-Path $DistDir 'qgb-v2.0-app-only.zip'
Write-Ok "可分发包已生成：$DistZip"

# ---------------------------------------------------------------- 汇总

$size = (Get-ChildItem -Recurse -File $ReleaseDir | Measure-Object -Property Length -Sum).Sum
$sizeMb = [math]::Round($size / 1MB, 1)
$fileCount = (Get-ChildItem -Recurse -File $ReleaseDir).Count

$zipMb = 0
if (Test-Path $DistZip) { $zipMb = [math]::Round((Get-Item $DistZip).Length / 1MB, 1) }

Write-Step '打包完成'
Write-Host "  发布目录 : $ReleaseDir"
Write-Host "  体积     : $sizeMb MB（$fileCount 个文件，含本机 data\）"
Write-Host "  可分发包 : $DistZip  ($zipMb MB)"
Write-Host ''
Write-Host '  分发给朋友的步骤：' -ForegroundColor Cyan
Write-Host "    1. 只发「qgb-v2.0-app-only.zip」这一个文件（GitHub Release 资产名统一用 ASCII）"
Write-Host '       （它已排除 data\、logs\、凭据库与状态库，并已扫描通过）'
Write-Host '    2. 对方解压后双击「启动搬运工.bat」'
Write-Host '    3. 按界面提示完成 群号 / 网盘 / QQ 三步配置'
Write-Host ''
Write-Host '  ⚠ 不要直接压缩整个发布文件夹！' -ForegroundColor Red
Write-Host '     那样会把 data\ 一起发出去 —— 里面有你的凭据加密库、'
Write-Host '     NapCat 配置和搬运状态。分发包请始终用上面那个 zip。' -ForegroundColor Yellow
Write-Host ''
