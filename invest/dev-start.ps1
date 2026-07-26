#!/usr/bin/env pwsh
# dev-start.ps1 — 开发模式一键启动
# 用法:  .\dev-start.ps1
# 前置:  已运行 .\setup-env.ps1

Set-StrictMode -Off
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot

# ── 自动补全工具路径（cargo / npm 可能不在默认 PATH）─────────────────────────
$cargoBin = "$env:USERPROFILE\.cargo\bin"
if ((Test-Path $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
    $env:PATH += ";$cargoBin"
}
# Node.js 常见安装位置
@(
    "C:\Program Files\nodejs",
    "$env:LOCALAPPDATA\Programs\nodejs",
    "$env:APPDATA\npm"
) | Where-Object { Test-Path $_ } | ForEach-Object {
    if ($env:PATH -notlike "*$_*") { $env:PATH += ";$_" }
}

Write-Host ""
Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   投资大师 — 开发模式启动              ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 刷新 PATH，确保 cargo/npm 可用 ──────────────────────────────────────────
$machinePath = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine')
$userPath    = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
$env:PATH    = "$env:USERPROFILE\.cargo\bin;$machinePath;$userPath"

# ── 查找工具路径 ─────────────────────────────────────────────────────────────
function Find-Exe([string]$Name, [string[]]$Extra = @()) {
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in $Extra) { if (Test-Path $p) { return $p } }
    return $null
}

$cargoPath = Find-Exe "cargo" @(
    "$env:USERPROFILE\.cargo\bin\cargo.exe",
    "D:\Users\pursu\.cargo\bin\cargo.exe"
)
$npmCmd = Find-Exe "npm" @(
    "C:\Program Files\nodejs\npm.cmd",
    "$env:PROGRAMFILES\nodejs\npm.cmd"
)

# ── 1. Python venv 检查 ─────────────────────────────────────────────────────
$venvPy = "$Root\invest-app\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[ERROR] 未找到 Python 虚拟环境，请先运行: .\setup-env.ps1" -ForegroundColor Red
    exit 1
}

# ── 2. 更新 Python 依赖 ──────────────────────────────────────────────────────
Write-Host "[1/4] 检查 Python 依赖..." -ForegroundColor Yellow
# 检查关键包是否已安装（包含全量依赖）
$checkPkgs = @("fastapi", "uvicorn", "multipart", "pandas", "yfinance", "numpy", "langgraph")
$missingPkg = $false
foreach ($pkg in $checkPkgs) {
    & $venvPy -c "import $pkg" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $missingPkg = $true; break
    }
}
if ($missingPkg) {
    Write-Host "  安装缺失依赖（从 requirements.txt）..." -ForegroundColor DarkGray
    # NO_PROXY=* 跳过 Windows 注册表代理（避免 pip 21 urllib3 TLS-over-TLS bug）
    $env:NO_PROXY = "*"
    & $venvPy -m pip install -r "$Root\invest-app\requirements.txt" -q
    if ($LASTEXITCODE -ne 0) {
        # WinError 32: plotly 等包被 VS Code/Pylance 锁住，跳过整体安装，逐个安装缺失包
        Write-Host "  ⚠ 全量安装失败（文件占用），尝试逐包安装..." -ForegroundColor Yellow
        $pkgMap = @{
            "pandas"     = "pandas>=2.0.0"
            "yfinance"   = "yfinance>=0.2.30"
            "numpy"      = "numpy>=1.24.0"
            "langgraph"  = "langgraph>=0.2.0"
            "rich"       = "rich>=13.0.0"
            "matplotlib" = "matplotlib>=3.7.0"
        }
        foreach ($pkg in $pkgMap.Keys) {
            & $venvPy -c "import $pkg" 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "    安装 $pkg ..." -ForegroundColor DarkGray
                & $venvPy -m pip install $pkgMap[$pkg] -q
            }
        }
    }
    Write-Host "  依赖已更新" -ForegroundColor DarkGray
} else {
    Write-Host "  依赖已就绪，跳过安装" -ForegroundColor DarkGray
}

# ── 3. 启动 FastAPI 后端 ─────────────────────────────────────────────────────
Write-Host "[2/4] 启动 FastAPI 后端 (http://127.0.0.1:8765)..." -ForegroundColor Yellow

# 先清理可能残留的旧 uvicorn 进程
$stalePort = netstat -ano | Select-String ":8765\s+.*LISTENING" | ForEach-Object {
    ($_ -split '\s+')[-1]
} | Sort-Object -Unique
if ($stalePort) {
    Write-Host "  清理残留进程 (PID: $($stalePort -join ', '))..." -ForegroundColor DarkGray
    $stalePort | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

$pythonJob = Start-Job -Name "invest-api" -ScriptBlock {
    param($root)
    Set-Location "$root\invest-app"
    & "$root\invest-app\.venv\Scripts\python.exe" -m uvicorn invest_app.server:app `
        --host 127.0.0.1 --port 8765 --reload --log-level info
} -ArgumentList $Root

Write-Host "  FastAPI Job ID: $($pythonJob.Id)  (后台运行)" -ForegroundColor DarkGray

# 等待后端启动（最多 8 秒）
$apiReady = $false
for ($i = 0; $i -lt 16; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/health" -TimeoutSec 1 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $apiReady = $true; break }
    } catch {}
}
if ($apiReady) {
    Write-Host "  ✓ FastAPI 已就绪  (默认账号: admin / admin123)" -ForegroundColor Green
} else {
    Write-Host "  ⚠ FastAPI 启动超时，请检查错误输出：" -ForegroundColor Yellow
    Receive-Job $pythonJob | Write-Host
}

# ── 4. 前端依赖 ──────────────────────────────────────────────────────────────
Write-Host "[3/4] 检查前端依赖..." -ForegroundColor Yellow
if (-not (Test-Path "$Root\ui\node_modules")) {
    if (-not $npmCmd) {
        Write-Host "  [ERROR] 未找到 npm，请先运行: .\setup-env.ps1" -ForegroundColor Red
        exit 1
    }
    Write-Host "  安装 npm 依赖 (首次需要 1-2 分钟)..." -ForegroundColor DarkGray
    Push-Location "$Root\ui"
    & $npmCmd install --silent
    Pop-Location
    Write-Host "  ✓ npm 依赖安装完成" -ForegroundColor Green
} else {
    Write-Host "  ✓ node_modules 已存在，跳过" -ForegroundColor DarkGray
}

# ── 5. 启动 Vite dev server（单独 Job，避免 tauri beforeDevCommand 找不到 npm）
$nodejsPath = "C:\Program Files\nodejs"
$viteJob = Start-Job -Name "invest-vite" -ScriptBlock {
    param($root, $nodePath)
    $env:PATH = "$nodePath;$env:PATH"
    Set-Location "$root\ui"
    & "$nodePath\npm.cmd" run dev
} -ArgumentList $Root, $nodejsPath

# 等待 Vite 就绪（最多 12 秒）
$viteReady = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1 -ErrorAction Stop
        $viteReady = $true; break
    } catch {}
}
if ($viteReady) {
    Write-Host "  ✓ Vite 开发服务器就绪 (http://127.0.0.1:5173)" -ForegroundColor Green
} else {
    Write-Host "  ⚠ Vite 启动超时，继续尝试..." -ForegroundColor Yellow
    Receive-Job $viteJob | Write-Host
}

# ── 6. 启动 Tauri 开发模式 ──────────────────────────────────────────────────
if (-not $cargoPath) {
    Write-Host "[ERROR] 未找到 cargo，请先运行: .\setup-env.ps1" -ForegroundColor Red
    exit 1
}
Write-Host "[4/4] 启动 Tauri 开发窗口..." -ForegroundColor Yellow
Write-Host "  提示: 关闭 Tauri 窗口 或按 Ctrl+C 终止所有服务" -ForegroundColor DarkGray
Write-Host ""

Push-Location "$Root\src-tauri"
& $cargoPath tauri dev
Pop-Location

# ── 清理 ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "正在停止后台服务..." -ForegroundColor Yellow
Stop-Job  -Name "invest-api","invest-vite" -ErrorAction SilentlyContinue
Remove-Job -Name "invest-api","invest-vite" -ErrorAction SilentlyContinue
Get-Process -Name "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -eq "" } |
    Stop-Process -ErrorAction SilentlyContinue
Write-Host "✓ 所有服务已停止" -ForegroundColor Green
