# cc-clean compatibility launcher (Windows) — forwards to "aik claude …".
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PassthroughArgs
)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$aik = Join-Path $scriptDir "aik.ps1"
$forwarded = @("claude") + $PassthroughArgs
& $aik @forwarded
exit $LASTEXITCODE
