# One-time helper: run Start-AllServices.ps1 when this user logs on.
# Docker Desktop and Ollama only run inside a logged-on session, so the trigger is logon rather than boot;
# enable Windows auto-logon if the machine should recover without anyone signing in.

param(
    [string]$TaskName = "LocaStudentStartup",
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "Start-AllServices.ps1"
if (-not (Test-Path $ScriptPath)) {
    throw "Missing $ScriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT${DelaySeconds}S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (at logon of $env:USERNAME, delay ${DelaySeconds}s)."
Write-Host "Run once now: powershell -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
