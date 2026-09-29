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

Write-Host ""
Write-Host "Done. Activate with:"
Write-Host "  conda activate zephie_rolling_on"
Write-Host "Then run:"
Write-Host "  python -m zephie_rolling_on.ui"
