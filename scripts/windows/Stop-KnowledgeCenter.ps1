# Stop Django service on the configured port.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Stop-KnowledgeCenter
Write-UpdateLog "Stop requested"
