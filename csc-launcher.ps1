# csc-launcher.ps1 (Windows)
#
# Usage:
#   ./csc-launcher.ps1              # opens Python TUI (no args)
#   ./csc-launcher.ps1 --dry-run    # passthrough to CLI
#

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PassthroughArgs
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    if (Get-Command "python" -ErrorAction SilentlyContinue) { return @("python") }
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command "python3" -ErrorAction SilentlyContinue) { return @("python3") }
    return $null
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetScript = Join-Path $scriptDir "codex-session-cloner.py"

if (-not (Test-Path $targetScript)) {
    Write-Host "Error: cannot find $targetScript" -ForegroundColor Red
    exit 1
}

$pyCmd = Resolve-PythonCommand
if (-not $pyCmd) {
    Write-Host "Error: Python not found. Install Python and ensure it's in PATH." -ForegroundColor Red
    exit 127
}

$pythonExe = $pyCmd[0]
$pythonPreArgs = @()
if ($pyCmd.Length -gt 1) {
    $pythonPreArgs = $pyCmd[1..($pyCmd.Length - 1)]
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Codex Session Cloner - Launcher (Windows)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ">> $pythonExe $($pythonPreArgs -join ' ') $targetScript $($PassthroughArgs -join ' ')" -ForegroundColor DarkGray

& $pythonExe @pythonPreArgs $targetScript @PassthroughArgs
exit $LASTEXITCODE
