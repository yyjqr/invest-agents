#!/usr/bin/env pwsh
# setup-env.ps1 — 首次环境初始化 (仅需运行一次)
# 用法:  .\setup-env.ps1
# 前置:  Python 3.11+  (已具备)
#        Node.js 18+   (见下方自动安装)
#        Rust stable   (见下方自动安装)

Set-StrictMode -Off
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot

# ── 自动补全工具路径（cargo / npm 可能不在默认 PATH）─────────────────────────
$cargoBin = "$env:USERPROFILE\.cargo\bin"
if ((Test-Path $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
    $env:PATH += ";$cargoBin"
}
@(
    "C:\Program Files\nodejs",
    "$env:LOCALAPPDATA\Programs\nodejs",
    "$env:APPDATA\npm"
) | Where-Object { Test-Path $_ } | ForEach-Object {
    if ($env:PATH -notlike "*$_*") { $env:PATH += ";$_" }
}

Write-Host ""
Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   投资大师 — 首次环境初始化            ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 辅助函数：刷新当前进程 PATH ─────────────────────────────────────────────
function Refresh-Path {
    $machinePath = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine')
    $userPath    = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    $env:PATH    = "$machinePath;$userPath"
}

function Find-Tool {
    param([string]$Name, [string[]]$ExtraPaths = @())
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($p in $ExtraPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ── 1. Node.js 检查与安装 ────────────────────────────────────────────────────
Write-Host "[Node.js] 检查安装状态..." -ForegroundColor Yellow

Refresh-Path
$nodeExtraPaths = @(
    "C:\Program Files\nodejs\node.exe",
    "$env:PROGRAMFILES\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
)
$nodePath = Find-Tool "node" $nodeExtraPaths
$npmCmd   = Find-Tool "npm"  @(
    "C:\Program Files\nodejs\npm.cmd",
    "$env:PROGRAMFILES\nodejs\npm.cmd"
)

if (-not $nodePath) {
    Write-Host "  ⚠ 未检测到 Node.js，正在通过 winget 自动安装..." -ForegroundColor Yellow
    # 尝试 winget 安装
    $winget = Find-Tool "winget"
    if ($winget) {
        & winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $nodePath = Find-Tool "node" $nodeExtraPaths
    }
    if (-not $nodePath) {
        # 尝试 choco
        $choco = Find-Tool "choco" @("C:\ProgramData\chocolatey\bin\choco.exe")
        if ($choco) {
            Write-Host "  通过 Chocolatey 安装 Node.js..." -ForegroundColor DarkGray
            & $choco install nodejs-lts -y --no-progress
            Refresh-Path
            $nodePath = Find-Tool "node" $nodeExtraPaths
        }
    }
    if (-not $nodePath) {
        Write-Host ""
        Write-Host "  [ERROR] 无法自动安装 Node.js，请手动安装后重新运行此脚本:" -ForegroundColor Red
        Write-Host "    下载地址: https://nodejs.org/zh-cn/download  (选 Windows Installer LTS)" -ForegroundColor White
        Write-Host "    安装完成后重启 PowerShell 再运行: .\setup-env.ps1" -ForegroundColor White
        Write-Host ""
        exit 1
    }
}

# 确保 npm.cmd 的目录加入当前进程 PATH
$nodeDir = Split-Path $nodePath
if ($env:PATH -notlike "*$nodeDir*") { $env:PATH = "$nodeDir;$env:PATH" }
$npmCmd = (Get-Command npm -ErrorAction SilentlyContinue)?.Source
if (-not $npmCmd) { $npmCmd = Join-Path $nodeDir "npm.cmd" }

$nodeVer = & $nodePath --version 2>&1
Write-Host "  ✓ Node.js $nodeVer  ($nodePath)" -ForegroundColor Green

# ── 2. Rust / Cargo 检查与安装 ──────────────────────────────────────────────
Write-Host ""
Write-Host "[Rust] 检查安装状态..." -ForegroundColor Yellow

$cargoExtraPaths = @(
    "$env:USERPROFILE\.cargo\bin\cargo.exe",
    "D:\Users\pursu\.cargo\bin\cargo.exe",
    "$env:LOCALAPPDATA\.cargo\bin\cargo.exe"
)
# 刷新 PATH 后再查找（rustup 安装后需要）
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
$cargoPath = Find-Tool "cargo" $cargoExtraPaths

if (-not $cargoPath) {
    Write-Host "  ⚠ 未检测到 Rust/Cargo，正在下载 rustup-init.exe 安装..." -ForegroundColor Yellow
    $rustupInstaller = "$env:TEMP\rustup-init.exe"
    try {
        Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $rustupInstaller -TimeoutSec 60
        & $rustupInstaller -y --default-toolchain stable 2>&1
        $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
        $cargoPath = Find-Tool "cargo" $cargoExtraPaths
    } catch {
        Write-Host "  下载失败: $_" -ForegroundColor Red
    }
    if (-not $cargoPath) {
        Write-Host ""
        Write-Host "  [ERROR] 无法自动安装 Rust，请手动安装后重新运行:" -ForegroundColor Red
        Write-Host "    下载地址: https://www.rust-lang.org/tools/install" -ForegroundColor White
        Write-Host "    安装完成后重启 PowerShell 再运行: .\setup-env.ps1" -ForegroundColor White
        Write-Host ""
        exit 1
    }
}

$cargoVer = & $cargoPath --version 2>&1
Write-Host "  ✓ $cargoVer  ($cargoPath)" -ForegroundColor Green

# ── 3. tauri-cli 检查与安装 ──────────────────────────────────────────────────
Write-Host ""
Write-Host "[Tauri] 检查 tauri-cli..." -ForegroundColor Yellow
$tauriCheck = & $cargoPath tauri --version 2>&1
if ($tauriCheck -match "tauri") {
    Write-Host "  ✓ $tauriCheck" -ForegroundColor Green
} else {
    Write-Host "  安装 tauri-cli v2 (首次需要几分钟编译)..." -ForegroundColor DarkGray
    & $cargoPath install tauri-cli --version "^2" 2>&1 | Write-Host
}

# ── 4. Python 虚拟环境 ───────────────────────────────────────────────────────
Write-Host ""
$venvDir = "$Root\invest-app\.venv"
$venvPy  = "$venvDir\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "[Python] 创建虚拟环境..." -ForegroundColor Yellow
    python -m venv $venvDir
    if (-not (Test-Path $venvPy)) {
        Write-Host "[ERROR] 虚拟环境创建失败，请确认 Python 3.11+ 已安装" -ForegroundColor Red
        exit 1
    }
    Write-Host "  ✓ 虚拟环境已创建" -ForegroundColor Green
} else {
    Write-Host "[Python] ✓ 虚拟环境已存在" -ForegroundColor Green
}

Write-Host "[Python] 安装依赖..." -ForegroundColor Yellow
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -r "$Root\invest-app\requirements.txt" -q
Push-Location "$Root\invest-app"
& $venvPy -m pip install -e . -q 2>$null
Pop-Location
Write-Host "  ✓ Python 依赖安装完成" -ForegroundColor Green

# ── 5. 前端 npm 依赖 ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[Node] 安装前端依赖..." -ForegroundColor Yellow
Push-Location "$Root\ui"
if ($npmCmd -and (Test-Path $npmCmd)) {
    & $npmCmd install
} else {
    npm install   # 刷新PATH后可能已可用
}
Pop-Location
Write-Host "  ✓ 前端依赖安装完成" -ForegroundColor Green

# ── 6. 初始化默认账号 ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[Data] 创建默认管理员账号..." -ForegroundColor Yellow
& $venvPy -c @"
import sys
sys.path.insert(0, r'$Root\invest-app')
from invest_app.auth import ensure_default_admin
ensure_default_admin()
print('  默认账号: admin / admin123')
"@

# ── 完成 ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   ✓ 初始化完成！                       ║" -ForegroundColor Green
Write-Host "║                                        ║" -ForegroundColor Green
Write-Host "║   启动开发模式:                        ║" -ForegroundColor Green
Write-Host "║     .\dev-start.ps1                    ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
