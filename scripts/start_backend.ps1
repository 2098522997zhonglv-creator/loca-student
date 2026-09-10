# Start backend for LAN access on Windows.
# Usage:
#   conda activate local-knowledge-center
#   .\scripts\start_backend.ps1

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Edit DJANGO_ADMIN_PASSWORD before first login."
}

python backend\manage.py runserver 0.0.0.0:8000
