# Run the full test suite with HTML + terminal coverage reports.
# Coverage output is written to htmlcov/index.html
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pytest tests/ -q --tb=short `
  --cov=apps `
  --cov=fileconverter `
  --cov-report=term-missing:skip-covered `
  --cov-report=html:htmlcov `
  --cov-fail-under=80

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Coverage report: htmlcov/index.html"
