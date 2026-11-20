# 在 Windows 上把项目打成单文件 EXE（可双击运行，目标机器无需装 Python）
#
# 用法（在项目根目录打开 PowerShell）：
#   .\scripts\build_windows.ps1
#   .\scripts\build_windows.ps1 -OneDir      # 打成一个文件夹（启动更快）
#   .\scripts\build_windows.ps1 -BuildPython 3.13   # 指定构建用 Python 版本或解释器路径
#
# 产物：dist\充电监控.exe
#
# 说明：PyInstaller/flet pack 不是交叉编译器，Windows 包必须在 Windows 上打。
#      构建虚拟环境用 Python 3.10~3.14：flet-cli 依赖 pyyaml，而 pyyaml 尚无
#      3.15 的预编译包（源码编译需要 MSVC），脚本会自动使用 uv 安装的 3.14。

param(
    [switch]$OneDir,
    [string]$BuildPython = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$appName = "充电监控"
$version = (Select-String -Path "pyproject.toml" -Pattern 'version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "==> 项目：$((Get-Location).Path)"
Write-Host "==> 版本：$version"

# 1) 构建用虚拟环境（解释器需为 3.10~3.14，原因见文件头说明）
function Find-BasePython {
    if ($BuildPython -and (Test-Path $BuildPython)) {
        return (Resolve-Path $BuildPython).Path
    }
    $uv = $null
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) { $uv = $uvCmd.Source }
    else {
        $uvLocal = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
        if (Test-Path $uvLocal) { $uv = $uvLocal }
    }
    $want = if ($BuildPython) { $BuildPython } else { "3.14" }
    if ($uv) {
        $p = & $uv python find $want 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return ("$p").Trim() }
    }
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $p = & py "-$want" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return ("$p").Trim() }
    }
    $pyAny = Get-Command python -ErrorAction SilentlyContinue
    if ($pyAny) { return $pyAny.Source }
    throw "找不到可用的 Python 解释器，请安装 uv 后重试，或用 -BuildPython 指定解释器路径"
}

$basePython = Find-BasePython
$baseVer = & $basePython -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$baseVer -ge [version]"3.15") {
    throw "构建解释器 $basePython 是 Python $baseVer，pyyaml 无该版本预编译包。请用 -BuildPython 指定 3.10~3.14 的解释器（推荐先执行 uv python install 3.14）"
}

$venvPython = ".\.venv-build\Scripts\python.exe"
if (Test-Path $venvPython) {
    $venvVer = & $venvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($venvVer -ne $baseVer) {
        Write-Host "==> .venv-build 是 Python $venvVer，与目标 $baseVer 不符，重建"
        Remove-Item -Recurse -Force ".venv-build"
    }
}
if (-not (Test-Path $venvPython)) {
    Write-Host "==> 创建构建虚拟环境 .venv-build（Python $baseVer）"
    & $basePython -m venv .venv-build
    if ($LASTEXITCODE -ne 0) { throw "创建 .venv-build 失败" }
}

function Install-Pkg([string]$desc, [string[]]$pkgArgs) {
    Write-Host "==> 安装 $desc"
    & $venvPython -m pip install @pkgArgs
    if ($LASTEXITCODE -ne 0) { throw "安装 $desc 失败（退出码 $LASTEXITCODE）" }
}
Install-Pkg "pip（升级）" @("--upgrade", "pip")
Install-Pkg "项目依赖" @("-e", ".")
Install-Pkg "pyinstaller" @("pyinstaller")
$fletVer = ("$((& $venvPython -m pip show flet | Select-String '^Version:'))" -split ":")[1].Trim()
Install-Pkg "flet-cli $fletVer（flet pack 需要）" @("flet-cli==$fletVer")

# 2) 图标：优先用 assets，只有 icon.png 时先用它生成 ico
$iconArgs = @()
if (-not (Test-Path "assets\icon.ico") -and (Test-Path "assets\icon.png")) {
    Write-Host "==> 由 assets\icon.png 生成 Windows 图标"
    & ".\.venv-build\Scripts\python.exe" "scripts\make_icon.py"
}
if (Test-Path "assets\icon.ico") {
    $iconArgs = @("--icon", "assets\icon.ico")
    Write-Host "==> 使用图标：assets\icon.ico"
} else {
    Write-Host "==> 没有可用图标，将使用默认图标"
}

# 3) 打包
$mode = @()
if ($OneDir) { $mode = @("--onedir") }

Write-Host "==> flet pack 开始（首次较慢）"
& ".\.venv-build\Scripts\flet.exe" pack "src\evcharge\cli.py" `
    --name $appName `
    --product-name $appName `
    --product-version $version `
    --file-version "$version.0" `
    --company-name "EVCharge" `
    --file-description "国网高速服务区充电监控与计费系统" `
    --add-data "assets;assets" `
    --hidden-import "matplotlib.backends.backend_agg" `
    --hidden-import "evcharge.presentation.pages.login_page" `
    @iconArgs @mode --yes
if ($LASTEXITCODE -ne 0) { throw "flet pack 失败（退出码 $LASTEXITCODE）" }
if (-not (Test-Path "dist")) { throw "打包结束但 dist\ 不存在，产物未生成" }

Write-Host ""
Write-Host "完成，产物在 dist\ 下："
Get-ChildItem dist | Format-Table Name, Length, LastWriteTime
Write-Host ""
Write-Host "提示：未签名的 EXE 首次运行会被 SmartScreen 提示；"
Write-Host "      点「更多信息」→「仍要运行」即可。"
