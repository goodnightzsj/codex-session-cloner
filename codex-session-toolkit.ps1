# Codex Session Toolkit launcher (Windows)

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PassthroughArgs
)

$ErrorActionPreference = "Stop"
$packageName = "codex_session_toolkit"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $scriptDir ".venv" }
$venvScriptsDir = Join-Path $venvDir "Scripts"
$installedExe = Join-Path $venvScriptsDir "codex-session-toolkit.exe"
$installedCmd = Join-Path $venvScriptsDir "codex-session-toolkit.cmd"
$srcDir = Join-Path $scriptDir "src"
$packageDir = Join-Path $srcDir $packageName
$launchMode = if ($env:CST_LAUNCH_MODE) { $env:CST_LAUNCH_MODE } elseif ($env:CSC_LAUNCH_MODE) { $env:CSC_LAUNCH_MODE } else { "auto" }
$isGitWorktree = (Test-Path (Join-Path $scriptDir ".git")) -and (Test-Path $packageDir)

function Resolve-PythonCommand {
    if (Get-Command "python" -ErrorAction SilentlyContinue) { return ,@("python") }
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return ,@("py", "-3") }
    if (Get-Command "python3" -ErrorAction SilentlyContinue) { return ,@("python3") }
    return $null
}

function Invoke-InstalledLauncher {
    if (Test-Path $installedExe) {
        Write-Host "=============================================" -ForegroundColor Cyan
        Write-Host " Codex Session Toolkit - Launcher (Local Venv)" -ForegroundColor Cyan
        Write-Host "=============================================" -ForegroundColor Cyan
        Write-Host ">> $installedExe $($PassthroughArgs -join ' ')" -ForegroundColor DarkGray
        & $installedExe @PassthroughArgs
        exit $LASTEXITCODE
    }

    if (Test-Path $installedCmd) {
        Write-Host "=============================================" -ForegroundColor Cyan
        Write-Host " Codex Session Toolkit - Launcher (Local Venv)" -ForegroundColor Cyan
        Write-Host "=============================================" -ForegroundColor Cyan
        Write-Host ">> $installedCmd $($PassthroughArgs -join ' ')" -ForegroundColor DarkGray
        & $installedCmd @PassthroughArgs
        exit $LASTEXITCODE
    }
}

Set-Location $scriptDir

if ($launchMode -eq "installed") {
    Invoke-InstalledLauncher
}

if ($launchMode -eq "auto" -and -not $isGitWorktree) {
    Invoke-InstalledLauncher
}

if (-not (Test-Path $packageDir)) {
    Invoke-InstalledLauncher
    Write-Host "Error: cannot find package source at $packageDir" -ForegroundColor Red
    Write-Host "Tip: run .\install.ps1 first, or keep the src\ tree intact." -ForegroundColor Yellow
    exit 1
}

$pyCmd = Resolve-PythonCommand
if (-not $pyCmd) {
    Write-Host "Error: Python not found. Install Python 3 first." -ForegroundColor Red
    Write-Host "Tip: after Python is ready, rerun .\install.ps1 ." -ForegroundColor Yellow
    exit 127
}

$pythonExe = $pyCmd[0]
$pythonPreArgs = @()
if ($pyCmd.Length -gt 1) {
    $pythonPreArgs = $pyCmd[1..($pyCmd.Length - 1)]
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Codex Session Toolkit - Launcher (Source Mode)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ">> $pythonExe $($pythonPreArgs -join ' ') -m $packageName $($PassthroughArgs -join ' ')" -ForegroundColor DarkGray

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcDir;$($env:PYTHONPATH)"
} else {
    $env:PYTHONPATH = $srcDir
}

& $pythonExe @pythonPreArgs -m $packageName @PassthroughArgs
exit $LASTEXITCODE
