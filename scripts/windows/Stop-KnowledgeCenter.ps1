# Stop Django service on the configured port, plus the review worker and celery beat.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Stop-CeleryBeat
Stop-ReviewWorker
Stop-KnowledgeCenter
Write-UpdateLog "Stop requested"
