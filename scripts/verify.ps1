$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

python -B -m py_compile `
  .\bian.py `
  .\server.py `
  .\src\bian_dashboard\analyzer.py `
  .\src\bian_dashboard\server.py `
  .\src\bian_dashboard\storage.py

node --check .\web\assets\charts.js

python -B .\bian.py --help | Out-Null

Get-ChildItem -Path $Root -Directory -Recurse -Filter "__pycache__" |
  Where-Object {
    $_.FullName -notlike (Join-Path $Root "archive*") -and
    $_.FullName -notlike (Join-Path $Root "backups*")
  } |
  Remove-Item -Recurse -Force

Write-Host "verify ok"
