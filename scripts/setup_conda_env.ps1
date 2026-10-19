# Create or update conda env zephie_rolling_on.
#   powershell -ExecutionPolicy Bypass -File scripts\setup_conda_env.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$conda = $null
$candidates = @()
$onPath = Get-Command conda -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Source }
foreach ($base in @($env:USERPROFILE, $env:ProgramData, "C:\")) {
    if (-not $base) { continue }
    $candidates += (Join-Path $base "miniconda3\Scripts\conda.exe")
    $candidates += (Join-Path $base "anaconda3\Scripts\conda.exe")
    $candidates += (Join-Path $base "miniconda3\condabin\conda.bat")
    $candidates += (Join-Path $base "anaconda3\condabin\conda.bat")
}
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) {
        $conda = $c
        break
    }
}
if (-not $conda) {
    throw "conda not found. Install Miniconda / Anaconda, or add conda to PATH, then retry."
}

Write-Host "Using: $conda"
Write-Host "Env file: $Root\environment.yml"

$envList = & $conda env list 2>$null | Out-String
if ($envList -match "(?m)^\s*zephie_rolling_on\s") {
    Write-Host "Updating existing env zephie_rolling_on ..."
    & $conda env update -f "$Root\environment.yml" --prune
} else {
    Write-Host "Creating env zephie_rolling_on ..."
    & $conda env create -f "$Root\environment.yml"
}
if ($LASTEXITCODE -ne 0) { throw "conda environment setup failed" }

# ORT variants share the same import directory. Updating requirements alone
# leaves the old CPU distribution installed and can later remove DML files.
$hasCpuRuntime = (& $conda run -n zephie_rolling_on python -c "import importlib.metadata as m; print(int(any(d.metadata['Name'].lower() == 'onnxruntime' for d in m.distributions())))" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "cannot inspect ONNX Runtime distributions" }
if ($hasCpuRuntime -eq "1") {
    Write-Host "Replacing conflicting CPU ONNX Runtime with DirectML..."
    & $conda run -n zephie_rolling_on python -m pip uninstall -y onnxruntime onnxruntime-directml
    if ($LASTEXITCODE -ne 0) { throw "ONNX Runtime cleanup failed; close running Python apps and retry" }
    & $conda run -n zephie_rolling_on python -m pip install "onnxruntime-directml>=1.24.4,<2"
    if ($LASTEXITCODE -ne 0) { throw "DirectML runtime install failed" }
}
& $conda run -n zephie_rolling_on python -c "import onnxruntime as o; print('ONNX Runtime', o.__version__, o.get_available_providers()); assert 'DmlExecutionProvider' in o.get_available_providers(), 'DirectML provider missing: reinstall onnxruntime-directml'"
if ($LASTEXITCODE -ne 0) { throw "DirectML runtime verification failed" }

Write-Host ""
Write-Host "Done. Activate with:"
Write-Host "  conda activate zephie_rolling_on"
Write-Host "Then run:"
Write-Host "  python -m zephie_rolling_on.ui"
