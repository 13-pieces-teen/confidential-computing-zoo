param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$DataFile = (Join-Path $PSScriptRoot 'results.json'),
    [string]$Python = 'python',
    [string]$TablePython = $Python
)
$ErrorActionPreference = 'Stop'
& $Python (Join-Path $PSScriptRoot 'render.py') --data $DataFile --out $OutputDirectory --table-python $TablePython
if ($LASTEXITCODE -ne 0) { throw 'Figure rendering failed' }
