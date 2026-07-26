<#
.SYNOPSIS
    下载自包含的 Python 嵌入式发行包，安装服务端依赖，并打包 Tauri 应用。

.DESCRIPTION
    步骤：
    1. 下载 Python Embeddable（独立可移植，无需目标机器安装 Python）
    2. 解压到 invest-app/python/，启用 site-packages，引导安装 pip
    3. 安装 requirements-server.txt（不含 dash / plotly / matplotlib / rich / click）
    4. 将 invest_app 包安装到嵌入式 Python 的 site-packages
    5. 运行 cargo tauri build

    ⚠️  与旧版 .venv 方案的区别：
        嵌入式 Python 包含完整 python3X.dll 及标准库，
        无需目标机器预装 Python，打包后可在其他 Windows 设备上直接运行。

.PARAMETER SkipBuild
    仅准备 Python 环境，不运行 cargo tauri build（调试用）。

.PARAMETER PyVersion
    嵌入式 Python 版本号，默认 3.12.9。

.EXAMPLE
    .\build-prod.ps1
    .\build-prod.ps1 -SkipBuild
    .\build-prod.ps1 -PyVersion 3.12.9
#>
param(
    [switch]$SkipBuild,
    [string]$PyVersion = "3.12.9"
)

$ErrorActionPreference = 'Stop'
$Root    = $PSScriptRoot
$AppDir  = Join-Path $Root 'invest-app'
$ReqFile = Join-Path $AppDir 'requirements-server.txt'

# ── 嵌入式 Python 配置 ─────────────────────────────────────────────────────
$PyArch     = "amd64"
$PyBaseName = "python-$PyVersion-embed-$PyArch"
$PyZipUrl   = "https://www.python.org/ftp/python/$PyVersion/$PyBaseName.zip"
$GetPipUrl  = "https://bootstrap.pypa.io/get-pip.py"
$EmbedDir   = Join-Path $AppDir 'python'
$PythonExe  = Join-Path $EmbedDir 'python.exe'

# ── 1. 验证前置条件 ─────────────────────────────────────────────────────────
if (-not (Test-Path $ReqFile)) {
    Write-Error "找不到 $ReqFile，请确认文件存在。"
}

Write-Host "目标 Python 版本：$PyVersion（嵌入式 $PyArch）" -ForegroundColor Cyan

# ── 2. 清理旧产物 ───────────────────────────────────────────────────────────
Write-Host "`n[1/3] 准备嵌入式 Python $PyVersion ..." -ForegroundColor Yellow

# 删除旧的嵌入式 Python 目录（如存在）
if (Test-Path $EmbedDir) {
    Remove-Item $EmbedDir -Recurse -Force
    Write-Host "  已删除旧 python/ 目录"
}
# 删除旧的 .venv（与新方案不再兼容；若被进程占用则跳过，不影响打包）
$OldVenv = Join-Path $AppDir '.venv'
if (Test-Path $OldVenv) {
    try {
        Remove-Item $OldVenv -Recurse -Force -ErrorAction Stop
        Write-Host "  已删除旧 .venv 目录"
    } catch {
        Write-Warning "  无法删除旧 .venv（可能有进程占用），已跳过：$($_.Exception.Message)"
    }
}

# ── 3. 下载并解压嵌入式 Python ─────────────────────────────────────────────
$ZipPath = Join-Path $env:TEMP "$PyBaseName.zip"
$ProgressPreference = 'SilentlyContinue'   # 禁用进度条，加快下载速度

Write-Host "  下载 $PyZipUrl ..."
$zipDownloaded = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        if ($attempt -gt 1) { Write-Host "  重试（第 $attempt 次）..." }
                Invoke-WebRequest -Uri $PyZipUrl -OutFile $ZipPath -UseBasicParsing -TimeoutSec 120 -NoProxy
        $zipDownloaded = $true
        break
    } catch {
        Write-Warning "  第 $attempt 次下载失败：$($_.Exception.Message)"
        if ($attempt -lt 3) { Start-Sleep -Seconds 3 }
    }
}
if (-not $zipDownloaded) {
    Write-Error "下载 Python 嵌入式包失败，已重试 3 次。请检查网络连接，或手动下载 $PyZipUrl 并解压至 $EmbedDir。"
}
Expand-Archive -Path $ZipPath -DestinationPath $EmbedDir -Force
Remove-Item $ZipPath
Write-Host "  解压完成：$EmbedDir"

# ── 4. 启用 site-packages（取消注释 ._pth 中的 import site）──────────────
$PthFile = Get-ChildItem $EmbedDir -Filter 'python*._pth' |
           Select-Object -First 1 -ExpandProperty FullName
if ($PthFile) {
    (Get-Content $PthFile -Raw) -replace '#import site', 'import site' |
        Set-Content $PthFile -NoNewline
    Write-Host "  已启用 site-packages（$([System.IO.Path]::GetFileName($PthFile))）"
} else {
    Write-Warning "  未找到 ._pth 文件，site-packages 或无法加载，请检查嵌入包内容。"
}

# ── 5. 引导安装 pip ─────────────────────────────────────────────────────────
Write-Host "  安装 pip ..."
$GetPipPath = Join-Path $env:TEMP 'get-pip.py'
$GetPipUrls = @(
    $GetPipUrl,
    "https://raw.githubusercontent.com/pypa/pip/main/contrib/get-pip.py"
)
$pipDownloaded = $false
foreach ($url in $GetPipUrls) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Write-Host "  下载 get-pip.py（来源：$url，第 $attempt 次）..."
            Invoke-WebRequest -Uri $url -OutFile $GetPipPath -UseBasicParsing -TimeoutSec 60 -NoProxy
            $pipDownloaded = $true
            break
        } catch {
            Write-Warning "  第 $attempt 次下载失败：$($_.Exception.Message)"
            if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
        }
    }
    if ($pipDownloaded) { break }
}
if (-not $pipDownloaded) {
    Write-Error "下载 get-pip.py 失败，已尝试所有来源及重试。请检查网络，或手动将 get-pip.py 放至 $GetPipPath。"
}
$ProgressPreference = 'Continue'

& $PythonExe $GetPipPath --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "pip 安装失败" }
Remove-Item $GetPipPath

Write-Host "  嵌入式 Python 就绪：$EmbedDir" -ForegroundColor Green

# ── 6. 安装 server-only 依赖 ─────────────────────────────────────────────────
Write-Host "`n[2/3] 安装生产依赖（requirements-server.txt）..." -ForegroundColor Yellow
& $PythonExe -m pip install --upgrade pip setuptools wheel --quiet --timeout 60 --retries 5

# 嵌入式 Python 下，部分仅提供 sdist 的依赖（如 yfinance 间接依赖的 multitasking）
# 会在 PEP 517 build isolation 阶段找不到 setuptools.build_meta。
# 这里预装构建工具并关闭隔离，改用当前嵌入式环境完成构建。
& $PythonExe -m pip install -r $ReqFile --timeout 60 --retries 5 --prefer-binary --no-build-isolation
if ($LASTEXITCODE -ne 0) { Write-Error "pip install 失败" }

# 将 invest_app 包本身直接复制到 site-packages（跳过 sdist 构建，无需 setuptools/wheel）
$SitePackages = Join-Path $EmbedDir 'Lib\site-packages'
$SrcPkg = Join-Path $AppDir 'invest_app'
$DstPkg = Join-Path $SitePackages 'invest_app'
if (Test-Path $DstPkg) { Remove-Item $DstPkg -Recurse -Force }
Copy-Item $SrcPkg $DstPkg -Recurse
Write-Host "  已复制 invest_app → $DstPkg" -ForegroundColor Green

# ── charset_normalizer 强制安装纯 Python 版 ────────────────────────────────
# 问题：pip 默认安装 mypyc 编译的 binary wheel，其中：
#   根级：*__mypyc*.pyd（mypyc 运行时共享库）
#   包内：charset_normalizer/cd.pyd、md.pyd（依赖根级 mypyc）
# 目标机上 AV 扫描或权限限制会导致这些 .pyd 加载失败，
# 但 Python 发现 .pyd 存在时不会 fallback 到对应 .py，
# 直接抛 ImportError → requests 找不到 charset_normalizer → 产生警告甚至崩溃。
#
# 修复方案：先 pip install 的是 binary wheel（依赖 requirements），
# 然后用 Python 自身确认并删除所有 charset_normalizer .pyd 文件，
# 确保 import charset_normalizer 走纯 Python 实现。
Write-Host "  清理 charset_normalizer 原生扩展（强制使用纯 Python）..." -ForegroundColor DarkGray

# 用 Python 自身执行删除（比 PowerShell Remove-Item 更可靠，
# site.getsitepackages() 返回实际安装路径，避免路径计算错误）
$cnCleanScript = @'
import glob, os, site, sys
removed = []
for sp in site.getsitepackages():
    # 删除 charset_normalizer 包目录内所有 .pyd
    for f in glob.glob(os.path.join(sp, "charset_normalizer", "*.pyd")):
        try:
            os.remove(f)
            removed.append(f)
        except OSError as e:
            print(f"WARN: cannot remove {f}: {e}", file=sys.stderr)
    # 删除根级 mypyc 共享库（其他包若依赖它也会失败，一并清除）
    for f in glob.glob(os.path.join(sp, "*__mypyc*.pyd")):
        try:
            os.remove(f)
            removed.append(f)
        except OSError as e:
            print(f"WARN: cannot remove {f}: {e}", file=sys.stderr)
if removed:
    print("Removed:", "\n  ".join([""] + removed))
else:
    print("No mypyc .pyd files found (already clean or not installed)")
'@
& $PythonExe -c $cnCleanScript
if ($LASTEXITCODE -ne 0) {
    Write-Warning "  charset_normalizer .pyd 清理脚本返回非零退出码"
}

# 验证 charset_normalizer 和 requests 均可正常导入
$importTest = & $PythonExe -W ignore -c @'
import charset_normalizer, requests
print("OK charset_normalizer:", charset_normalizer.__version__)
print("OK requests:", requests.__version__)
'@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  $importTest" -ForegroundColor Green
} else {
    Write-Warning "  导入验证失败，详情：$importTest"
    Write-Warning "  尝试强制重装纯 Python 版 charset_normalizer ..."
    # 最后手段：从 sdist 重装（charset_normalizer sdist 是纯 Python，无需编译器）
    & $PythonExe -m pip install "charset-normalizer" `
        --force-reinstall --no-binary=charset-normalizer --no-deps `
        --quiet --timeout 60 --retries 3
    $importTest2 = & $PythonExe -W ignore -c "import charset_normalizer; print('OK:', charset_normalizer.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  重装后验证通过：$importTest2" -ForegroundColor Green
    } else {
        Write-Warning "  charset_normalizer 仍然无法导入：$importTest2（继续打包，目标机可能出现警告）"
    }
}
$RemovedOptionalNative = 0   # 保留变量供下面代码引用

# ── 移除仅在开发/热重载模式下使用的包 ────────────────────────────────────────
# watchfiles  : 仅供 uvicorn --reload 使用；生产启动命令不含 --reload
# httptools   : 不可卸载！pip uninstall 只删除原生 .pyd，Python stub 会残留，
#               导致 httptools 可导入但缺少 HttpRequestParser attribute，
#               uvicorn 误判 httptools 可用并在运行期抛 AttributeError。
#               保留 httptools 可让 uvicorn 获得原生 HTTP 解析性能。
# 注意: curl_cffi / cffi 不能移除 —— yfinance 1.x 启动时强依赖 curl_cffi
Write-Host "  卸载仅开发用包（watchfiles）..." -ForegroundColor DarkGray
& $PythonExe -m pip uninstall watchfiles -y --quiet 2>$null

# ── 移除各库的测试/可选扩展 .pyd（不影响生产运行）──────────────────────────
Write-Host "  清理 numpy / pandas 可选 / protobuf 测试扩展 .pyd ..." -ForegroundColor DarkGray
$RemovedTestPyds = 0

# numpy/_core 测试扩展
$numpyCoreDir = Join-Path $SitePackages 'numpy\_core'
foreach ($stem in @('_multiarray_tests', '_rational_tests', '_struct_ufunc_tests', '_umath_tests', '_operand_flag_tests')) {
    foreach ($ext in @('.pyd', '.lib')) {
        $fp = Join-Path $numpyCoreDir "$stem.cp312-win_amd64$ext"
        if (Test-Path $fp) { Remove-Item $fp -Force; $RemovedTestPyds++ }
    }
}

# pandas._libs.testing 不能删除：pandas 顶层 import 会经过 pandas.testing → pandas._libs.testing
# 删除后目标机上 `import pandas` 直接崩溃，导致 FastAPI 后端无法启动。
# pandas._libs.sas 仅供 read_sas 使用，本项目不依赖，可继续裁剪。
$pandasSasPyd = Join-Path $SitePackages 'pandas\_libs\sas.cp312-win_amd64.pyd'
if (Test-Path $pandasSasPyd) { Remove-Item $pandasSasPyd -Force; $RemovedTestPyds++ }

# protobuf 原生扩展（_message.pyd）── protobuf 自动回退到纯 Python 实现
$msgPyd = Join-Path $SitePackages 'google\_upb\_message.pyd'
if (Test-Path $msgPyd) { Remove-Item $msgPyd -Force; $RemovedTestPyds++ }

if ($RemovedTestPyds -gt 0) {
    Write-Host "  已移除 $RemovedTestPyds 个测试/可选扩展 .pyd" -ForegroundColor Green
}

# 验证关键运行时导入，避免把缺失原生扩展的坏包继续打进安装程序
Write-Host "  验证关键运行时导入 ..." -ForegroundColor DarkGray
$runtimeImportTest = & $PythonExe -W ignore -c @'
import fastapi
import numpy
import pandas
import uvicorn
import yfinance
import invest_app.server
print("OK fastapi:", fastapi.__version__)
print("OK numpy:", numpy.__version__)
print("OK pandas:", pandas.__version__)
print("OK uvicorn:", uvicorn.__version__)
print("OK yfinance:", yfinance.__version__)
print("OK invest_app.server")
'@ 2>&1
$runtimeImportOutput = ($runtimeImportTest | Out-String).Trim()
if ($LASTEXITCODE -eq 0) {
    Write-Host "  $runtimeImportOutput" -ForegroundColor Green
} else {
    Write-Error "关键运行时导入验证失败：`n$runtimeImportOutput"
}

Write-Host "  依赖安装完成" -ForegroundColor Green

# 清理 __pycache__（减小包体积，目标机上也无需缓存）
Write-Host "  清理 __pycache__ ..."
Get-ChildItem $AppDir -Recurse -Directory -Filter '__pycache__' |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  __pycache__ 清理完成" -ForegroundColor Green

# 显示体积估算
$EmbedSizeMB = [math]::Round((Get-ChildItem $EmbedDir -Recurse -File |
    Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host "  python/ 体积：${EmbedSizeMB} MB"

# ── 7. Tauri 打包 ──────────────────────────────────────────────────────────
if ($SkipBuild) {
    Write-Host "`n已跳过 cargo tauri build（-SkipBuild）" -ForegroundColor DarkGray
    exit 0
}

# 先构建前端（避免依赖 tauri.conf.json 的 beforeBuildCommand，绕过 shell 路径问题）
$UiDir = Join-Path $Root 'ui'
Write-Host "`n[3/4] 构建前端（npm run build）..." -ForegroundColor Yellow
Push-Location $UiDir
try {
    if (-not (Test-Path 'node_modules')) {
        Write-Host "  node_modules 不存在，先执行 npm install ..."
        npm install
        if ($LASTEXITCODE -ne 0) { Write-Error "npm install 失败" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { Write-Error "npm run build 失败" }
} finally {
    Pop-Location
}
Write-Host "  前端构建完成" -ForegroundColor Green

Write-Host "`n[4/4] cargo tauri build ..." -ForegroundColor Yellow
Push-Location (Join-Path $Root 'src-tauri')
try {
    cargo tauri build
    if ($LASTEXITCODE -ne 0) { Write-Error "cargo tauri build 失败" }
} finally {
    Pop-Location
}

$BundleDir = Join-Path $Root 'src-tauri\target\release\bundle'
if (Test-Path $BundleDir) {
    $BundleSizeMB = [math]::Round((Get-ChildItem $BundleDir -Recurse -File |
        Measure-Object -Property Length -Sum).Sum / 1MB, 0)
    Write-Host "`n打包完成！Bundle 体积：${BundleSizeMB} MB" -ForegroundColor Green
    Write-Host "输出目录：$BundleDir"
} else {
    Write-Host "`n打包完成！" -ForegroundColor Green
}
