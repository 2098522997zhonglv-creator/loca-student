# Stop Django service on the configured port, plus the review worker.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Stop-ReviewWorker
Stop-KnowledgeCenter
Write-UpdateLog "Stop requested"
