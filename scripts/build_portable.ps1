# Build portable onedir folder for distribution (no model packages included).
#   conda activate zephie_rolling_on
#   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
#
# Size notes: opencv-python-headless (no ffmpeg/GUI); strip onnxruntime extras;
# the OCR engine still needs its models collected (see the RapidOCR note below).

param(
    # Optional: model_host.exe to bundle. It is a separate runtime component and
    # is not part of this repository.
    [string]$ModelHostExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python not on PATH (activate zephie_rolling_on first)" }

$hasPyI = $false
try {
    & python -c "import PyInstaller" 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) { $hasPyI = $true }
} catch {
    $hasPyI = $false
}
if (-not $hasPyI) {
    Write-Host "Installing pyinstaller..."
    & python -m pip install "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }
}

# Prefer headless OpenCV (same cv2 API for matchTemplate / OCR preprocess).
& python -c "import cv2; print(cv2.__file__)"
$cvFile = & python -c "import cv2; print(cv2.__file__)"
if ($cvFile -match "opencv.python" -and $cvFile -notmatch "headless") {
    Write-Host "Switching opencv-python -> opencv-python-headless..."
    & python -m pip uninstall -y opencv-python
    & python -m pip install "opencv-python-headless>=4.8.0"
    if ($LASTEXITCODE -ne 0) { throw "opencv-python-headless install failed" }
}

$Version = "0.9.3"
$distName = "ZephieRollingOn!"
$workDist = Join-Path $Root "dist\$distName"
$outDir = Join-Path $Root "dist\ZephieRollingOn_v$Version"
$entry = Join-Path $Root "src\zephie_rolling_on\ui\__main__.py"
$iconIco = Join-Path $Root "assets\ui\zehpie_window_icon.ico"
$rthTcl = Join-Path $Root "scripts\pyi_rth_tcl_tk.py"

if (-not (Test-Path $iconIco)) { throw "Missing exe icon: $iconIco" }

# Avoid host conda base Tcl leaking into the frozen app search path.
$env:TCL_LIBRARY = $null
$env:TK_LIBRARY = $null
Remove-Item Env:TCL_LIBRARY -ErrorAction SilentlyContinue
Remove-Item Env:TK_LIBRARY -ErrorAction SilentlyContinue

# Windowed app: request administrator from the very start.
# Window capture, input injection and game-window access all need elevation;
# without it, clicks are silently dropped by the game. The flag embeds a
# requireAdministrator manifest, so Windows shows the UAC prompt on launch.
Write-Host "PyInstaller onedir (requires administrator)..."
& python -m PyInstaller `
    --noconfirm --clean --windowed --onedir `
    --name $distName `
    --uac-admin `
    --paths (Join-Path $Root "src") `
    --icon $iconIco `
    --runtime-hook $rthTcl `
    --collect-all rapidocr `
    --collect-binaries onnxruntime `
    --collect-data onnxruntime `
    --exclude-module easyocr `
    --exclude-module torch `
    --exclude-module torchvision `
    --exclude-module torio `
    --exclude-module tensorflow `
    --exclude-module matplotlib `
    --exclude-module scipy `
    --exclude-module pandas `
    --hidden-import win32timezone `
    --hidden-import pythoncom `
    $entry

if (-not (Test-Path (Join-Path $workDist "$distName.exe"))) {
    throw "Build failed: exe not found under $workDist"
}

$internal = Join-Path $workDist "_internal"

# Drop heavy / unused trees that --collect-* may still pull in.
$stripDirs = @(
    "onnxruntime\quantization",
    "onnxruntime\tools",
    "onnxruntime\datasets",
    "cv2\opencv_videoio_ffmpeg*.dll"
)
foreach ($rel in @(
    "onnxruntime\quantization",
    "onnxruntime\tools",
    "onnxruntime\datasets"
)) {
    $p = Join-Path $internal $rel
    if (Test-Path $p) {
        Write-Host "Strip $rel"
        Remove-Item -Recurse -Force $p
    }
}
Get-ChildItem (Join-Path $internal "cv2") -Filter "opencv_videoio_ffmpeg*.dll" -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "Strip $($_.Name)"; Remove-Item -Force $_.FullName }

# Align Tcl/Tk data + DLLs with the build env (fixes 8.6.15 vs 8.6.13 mismatch).
$pyPrefix = & python -c "import sys; print(sys.prefix)"
$envTcl = Join-Path $pyPrefix "Library\lib\tcl8.6"
$envTk = Join-Path $pyPrefix "Library\lib\tk8.6"
$envBin = Join-Path $pyPrefix "Library\bin"
if (Test-Path $envTcl) {
    $dstTcl = Join-Path $internal "_tcl_data"
    Write-Host "Sync _tcl_data from $envTcl"
    if (Test-Path $dstTcl) { Remove-Item -Recurse -Force $dstTcl }
    Copy-Item -Recurse -Force $envTcl $dstTcl
}
if (Test-Path $envTk) {
    $dstTk = Join-Path $internal "_tk_data"
    Write-Host "Sync _tk_data from $envTk"
    if (Test-Path $dstTk) { Remove-Item -Recurse -Force $dstTk }
    Copy-Item -Recurse -Force $envTk $dstTk
}
# tcl86t from conda needs zlib1.dll (PyInstaller often only ships zlib.dll).
foreach ($dll in @("tcl86t.dll", "tk86t.dll", "zlib1.dll", "zlib.dll", "liblzma.dll")) {
    $srcDll = Join-Path $envBin $dll
    if (Test-Path $srcDll) {
        Write-Host "Sync $dll"
        Copy-Item -Force $srcDll (Join-Path $internal $dll)
    }
}

Write-Host "Assembling portable folder (exe-adjacent resources)..."
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Path $outDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $workDist "*") $outDir

foreach ($name in @("config", "assets")) {
    $src = Join-Path $Root $name
    $dst = Join-Path $outDir $name
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $src $dst
}

# The build machine's own per-user state must never ship as the factory default.
$userState = @(
    "ui_geometry.yaml", "game_window.yaml", "planner_state.yaml",
    "hotkeys.yaml", "auto_click.yaml"
)
foreach ($f in $userState) {
    $p = Join-Path $outDir "config\$f"
    if (Test-Path $p) {
        Write-Host "Excluding local user config: config\$f"
        Remove-Item -Force $p
    }
}

# Runtime state the app writes next to itself: the map cache is regenerated on
# first run, and data/ holds debug dumps. Neither belongs in a fresh package.
foreach ($f in @("map.xlsx.cache.json", "check_report.txt")) {
    $p = Join-Path $outDir $f
    if (Test-Path $p) {
        Write-Host "Excluding runtime artifact: $f"
        Remove-Item -Force $p
    }
}
$dataDir = Join-Path $outDir "data"
if (Test-Path $dataDir) {
    Write-Host "Excluding runtime artifact: data\"
    Remove-Item -Recurse -Force $dataDir
}
# Nothing tracked may be hidden by the exclusions above.
foreach ($f in @("auto_click.default.yaml", "regions.yaml", "click_targets.yaml",
                 "adventure_frame.yaml", "dev.yaml")) {
    if (-not (Test-Path (Join-Path $outDir "config\$f"))) {
        throw "config\$f missing from the portable build"
    }
}
Copy-Item -Force (Join-Path $Root "map.xlsx") $outDir
if (Test-Path (Join-Path $Root "map_info.txt")) {
    Copy-Item -Force (Join-Path $Root "map_info.txt") $outDir
}
Copy-Item -Force (Join-Path $Root "LICENSE") $outDir

$dm = Join-Path $outDir "decision_models"
New-Item -ItemType Directory -Force -Path $dm | Out-Null
Copy-Item -Force (Join-Path $Root "decision_models\README.md") $dm

# Runtime component that serves .zm packages. It is not part of this
# repository; pass its path (or have it beside the build root) to bundle it.
if (-not $ModelHostExe) {
    $candidates = @(
        (Join-Path $Root "model_host.exe"),
        (Join-Path $Root "dist\model_host.exe")
    )
    $ModelHostExe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($ModelHostExe -and (Test-Path $ModelHostExe)) {
    Write-Host "Bundling model host: $ModelHostExe"
    Copy-Item -Force $ModelHostExe (Join-Path $outDir "model_host.exe")
} else {
    Write-Warning "model_host.exe not found — .zm packages will not load"
}

# Open-source model engine: required to import .vpk packages.
$velaSrc = Join-Path $Root "native\vela"
$velaDst = Join-Path $outDir "native\vela"
$velaPyd = Get-ChildItem $velaSrc -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "vela_official*.pyd" -or $_.Name -like "vela_official*.so" }
if ($velaPyd) {
    New-Item -ItemType Directory -Force -Path $velaDst | Out-Null
    $velaPyd | ForEach-Object {
        Write-Host "Bundling native model engine: $($_.Name)"
        Copy-Item -Force $_.FullName $velaDst
    }
} else {
    Write-Warning "native/vela/vela_official*.pyd not found — .vpk models will not import"
}

# Model packages are NOT bundled: they are distributed separately and dropped
# into decision_models/ by the user.
Get-ChildItem $dm -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".zm", ".vpk", ".dll", ".so", ".dylib" } |
    Remove-Item -Force

Copy-Item -Force (Join-Path $Root "docs\USER_README_ZH.txt") (Join-Path $outDir "README_ZH.txt")

# Standalone environment self-check; not run on normal startup.
$checkBat = Join-Path $outDir "自检.bat"
$batBody = @"
@echo off
cd /d "%~dp0"
"$distName.exe" --check
"@
Set-Content -Path $checkBat -Value $batBody -Encoding Default
Write-Host "Wrote self-check launcher: 自检.bat"

# The intermediate PyInstaller output is not part of the deliverable.
if (Test-Path $workDist) { Remove-Item -Recurse -Force $workDist }

$total = (Get-ChildItem $outDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host ("Done: {0}  ({1:N1} MB)" -f $outDir, ($total / 1MB))
Write-Host "Zip that folder. Model packages are distributed separately."
