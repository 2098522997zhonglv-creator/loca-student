# Start Django service (0.0.0.0:8000 by default) and the review worker.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Set-Location $script:RepoRoot
Start-KnowledgeCenter
Start-ReviewWorker
