# Boot-time startup: dependencies first (Ollama, Qdrant, Xinference), then the knowledge center and actuator.
# Every step is idempotent and a failed step does not block the rest.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
Set-Location $script:RepoRoot
Ensure-LogDir
Write-UpdateLog "==== boot start ===="

$steps = @(
    @{ Name = "Ollama"; Action = { Start-Ollama } },
    @{ Name = "Qdrant"; Action = { Start-QdrantContainer } },
    @{ Name = "Xinference"; Action = { Start-XinferenceReranker } },
    @{ Name = "Skills"; Action = { Sync-BundledSkills } },
    @{ Name = "Daphne"; Action = { Start-KnowledgeCenter } },
    @{ Name = "Celery worker"; Action = { Start-ReviewWorker } },
    @{ Name = "Celery beat"; Action = { Start-CeleryBeat } },
    @{ Name = "Actuator"; Action = { Start-Actuator } }
)

foreach ($step in $steps) {
    try {
        & $step.Action
    } catch {
        Write-UpdateLog "$($step.Name) failed: $($_.Exception.Message)"
    }
}

Write-UpdateLog "==== boot done ===="
