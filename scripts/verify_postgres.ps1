# PostgreSQL pre-prod verification (mirrors CI test-postgres job).
# Requires Docker Desktop running. Usage: .\scripts\verify_postgres.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Step([string]$Label, [scriptblock]$Block) {
    Write-Host "==> $Label"
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Label (exit $LASTEXITCODE)" }
}

Invoke-Step "Starting PostgreSQL (docker compose)" {
    docker compose up postgres -d --wait
}

$env:FILECONVERTER_TEST_POSTGRES = "1"
$env:DATABASE_URL = "postgres://fileconverter:fileconverter@localhost:5433/fileconverter"
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_PORT = "5433"
$env:POSTGRES_DB = "fileconverter"
$env:POSTGRES_USER = "fileconverter"
$env:POSTGRES_PASSWORD = "fileconverter"
$env:DJANGO_DEBUG = "True"
$env:DJANGO_SECRET_KEY = "local-pg-verify-secret"
$env:CELERY_TASK_ALWAYS_EAGER = "True"
$env:FILECONVERTER_USE_REDIS_CACHE = "False"

Invoke-Step "Installing dependencies" { pip install -q -r requirements.lock "psycopg[binary]" }
Invoke-Step "migrate --noinput" { python manage.py migrate --noinput }
Invoke-Step "makemigrations --check" { python manage.py makemigrations --check --dry-run }

$env:DJANGO_DEBUG = "False"
$env:DJANGO_SECRET_KEY = "local-pg-verify-strong-secret-key-0123456789"
Invoke-Step "check --deploy" { python manage.py check --deploy }
$env:DJANGO_DEBUG = "True"
$env:DJANGO_SECRET_KEY = "local-pg-verify-secret"

Invoke-Step "pytest (PostgreSQL + RLS)" { python -m pytest tests/ -q --tb=short }

Write-Host "==> All PostgreSQL verification steps passed."
