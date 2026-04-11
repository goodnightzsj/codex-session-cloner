param(
    [switch] $Editable,
    [switch] $Force,
    [Alias("h")]
    [switch] $Help,
    [string] $Python
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$venvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $projectRoot ".venv" }

function Show-Usage {
    @"
Usage: .\install.ps1 [-Editable] [-Force] [-Python <python-bin>]

Options:
  -Editable        Install in editable mode for local development
  -Force           Recreate the local .venv before installing
  -Python <bin>    Use a specific Python executable
"@
}

function Resolve-PythonCommand {
    param([string] $PreferredPython)

    if ($PreferredPython) {
        return ,@($PreferredPython)
    }
    if (Get-Command "python" -ErrorAction SilentlyContinue) { return ,@("python") }
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return ,@("py", "-3") }
    if (Get-Command "python3" -ErrorAction SilentlyContinue) { return ,@("python3") }
    return $null
}

function Assert-LastExitCode {
    param([string] $StepName)

    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE."
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

$pyCmd = Resolve-PythonCommand -PreferredPython $Python
if (-not $pyCmd) {
    Write-Host "Error: Python not found. Install Python 3 first." -ForegroundColor Red
    exit 127
}

if ($Force -and (Test-Path $venvDir)) {
    Remove-Item -Recurse -Force $venvDir
}

$pythonExe = $pyCmd[0]
$pythonPreArgs = @()
if ($pyCmd.Length -gt 1) {
    $pythonPreArgs = $pyCmd[1..($pyCmd.Length - 1)]
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Codex Session Toolkit - Installer (Windows)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Project:   $projectRoot"
Write-Host "Python:    $pythonExe $($pythonPreArgs -join ' ')"
Write-Host "Venv:      $venvDir"
if ($Editable) {
    Write-Host "Mode:      editable"
} else {
    Write-Host "Mode:      standard"
}

& $pythonExe @pythonPreArgs -m venv $venvDir --system-site-packages
Assert-LastExitCode "python -m venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Error: failed to create local venv at $venvDir" -ForegroundColor Red
    exit 1
}

& $venvPython -c "import setuptools" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: setuptools is not available for the local installer environment." -ForegroundColor Red
    Write-Host "Tip: install setuptools for your base Python, then rerun .\install.ps1 ." -ForegroundColor Yellow
    exit 1
}

try {
    if ($Editable) {
        & $venvPython -m pip install --no-deps --no-build-isolation -e $projectRoot
    } else {
        & $venvPython -m pip install --no-deps --no-build-isolation $projectRoot
    }
    Assert-LastExitCode "pip install --no-build-isolation"
} catch {
    Write-Host "Local no-build-isolation install failed; retrying with build isolation..." -ForegroundColor Yellow
    if ($Editable) {
        & $venvPython -m pip install --no-deps -e $projectRoot
    } else {
        & $venvPython -m pip install --no-deps $projectRoot
    }
    Assert-LastExitCode "pip install"
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Run now:"
Write-Host "  .\codex-session-toolkit.cmd"
Write-Host "Version:"
Write-Host "  .\.venv\Scripts\codex-session-toolkit.exe --version"
