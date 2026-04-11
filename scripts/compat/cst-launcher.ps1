param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PassthroughArgs
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
& (Join-Path $projectRoot "codex-session-toolkit.ps1") @PassthroughArgs
exit $LASTEXITCODE
