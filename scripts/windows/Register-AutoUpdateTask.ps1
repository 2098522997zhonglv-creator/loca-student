# One-time helper: register a Task Scheduler job that runs Update-And-Restart.ps1 every 5 minutes.
# Run PowerShell as the same Windows user that can git pull (with saved GitHub credentials).

param(
    [string]$TaskName = "LocaStudentAutoUpdate",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "Update-And-Restart.ps1"
if (-not (Test-Path $ScriptPath)) {
    throw "Missing $ScriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' every $IntervalMinutes minutes."
Write-Host "Script: $ScriptPath"
Write-Host "Run once now: powershell -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
