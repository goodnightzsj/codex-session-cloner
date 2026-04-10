param(
    [switch] $Editable,
    [switch] $Force,
    [Alias("h")]
    [switch] $Help,
    [string] $Python
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptDir "scripts\install\install.windows.ps1") `
    -Editable:$Editable `
    -Force:$Force `
    -Help:$Help `
    -Python $Python
exit $LASTEXITCODE
