$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Invoke-NativeChecked {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
  )
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$Label failed with exit code $LASTEXITCODE"
  }
}

Invoke-NativeChecked "python py_compile" python -B -m py_compile `
  .\bian.py `
  .\server.py `
  .\src\bian_dashboard\analyzer.py `
  .\src\bian_dashboard\server.py `
  .\src\bian_dashboard\storage.py `
  .\scripts\deploy.py `
  .\scripts\smoke.py

Invoke-NativeChecked "node syntax check" node --check .\web\assets\charts.js

Invoke-NativeChecked "bian help" python -B .\bian.py --help | Out-Null

Invoke-NativeChecked "offline smoke tests" python -B .\scripts\smoke.py

Get-ChildItem -Path $Root -Directory -Recurse -Filter "__pycache__" |
  Where-Object {
    $_.FullName -notlike (Join-Path $Root "archive*") -and
    $_.FullName -notlike (Join-Path $Root "backups*")
  } |
  Remove-Item -Recurse -Force

Write-Host "verify ok"
