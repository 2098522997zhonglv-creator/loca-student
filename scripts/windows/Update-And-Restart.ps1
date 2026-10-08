# Pull latest main, rebuild frontend when needed, keep Django running.
# Intended for Windows Task Scheduler every few minutes.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Set-Location $script:RepoRoot
Ensure-LogDir
Write-UpdateLog "==== auto-update start ===="

if (-not (Test-Path (Join-Path $script:RepoRoot ".git"))) {
    throw "Not a git repo: $script:RepoRoot"
}

$before = (git rev-parse HEAD).Trim()
Write-UpdateLog "Current HEAD=$before"

git fetch $script:Remote $script:Branch
if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }

git pull --ff-only $script:Remote $script:Branch
if ($LASTEXITCODE -ne 0) { throw "git pull failed" }

$after = (git rev-parse HEAD).Trim()

# 和「正在运行的服务」比，而不是只和本轮拉取前比：手动 git pull 后也要重启。
$running = Get-RunningCommit
$serviceUp = (Get-ListenPids -Port $script:Port).Count -gt 0
$runningUnknown = $serviceUp -and -not $running
$base = if ($running) { $running } else { $before }
$changed = ($base -ne $after) -or $runningUnknown
Write-UpdateLog "After pull HEAD=$after running=$(if ($running) { $running } else { 'unknown' }) changed=$changed"

$needFrontendBuild = $false
$needPipInstall = $false
$needMigrate = $false

if ($runningUnknown) {
    # 服务由旧版脚本启动、没有记录提交：无法算差异，按全量更新处理一次。
    $needFrontendBuild = $true
    $needMigrate = $true
} elseif ($changed) {
    $diffFiles = git diff --name-only "$base" "$after"
    if ($diffFiles | Select-String -Pattern '^(frontend/|package-lock\.json)') {
        $needFrontendBuild = $true
    }
    if ($diffFiles | Select-String -Pattern '^(requirements\.txt|backend/requirements\.txt|environment\.yml)') {
        $needPipInstall = $true
    }
    if ($diffFiles | Select-String -Pattern '^backend/.*/migrations/') {
        $needMigrate = $true
    }
    # First-time or missing dist after pull of frontend changes
    if (-not (Test-Path (Join-Path $script:RepoRoot "frontend\dist\index.html"))) {
        $needFrontendBuild = $true
    }
} elseif (-not (Test-Path (Join-Path $script:RepoRoot "frontend\dist\index.html"))) {
    $needFrontendBuild = $true
}

if ($needPipInstall) {
    Write-UpdateLog "Installing Python requirements..."
    & $script:PythonExe -m pip install -r (Join-Path $script:RepoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

if ($needMigrate) {
    Write-UpdateLog "Running Django migrations..."
    Push-Location (Join-Path $script:RepoRoot "backend")
    try {
        & $script:PythonExe manage.py migrate --noinput
        if ($LASTEXITCODE -ne 0) { throw "migrate failed" }
    } finally {
        Pop-Location
    }
}

if ($needFrontendBuild) {
    Invoke-FrontendBuild
}

Sync-BundledSkills

if ($changed) {
    Write-UpdateLog "Code changed, restarting Django, worker and beat..."
    Stop-CeleryBeat
    Stop-ReviewWorker
    Stop-KnowledgeCenter
    Start-KnowledgeCenter
    Start-ReviewWorker
    Start-CeleryBeat
} else {
    $listening = Get-ListenPids -Port $script:Port
    if ($listening.Count -eq 0) {
        Write-UpdateLog "No update, but service down — starting Django..."
        Start-KnowledgeCenter
    } else {
        Write-UpdateLog "No update, service already running"
    }
    # worker / beat 可能自己挂掉，单独补起
    Start-ReviewWorker
    Start-CeleryBeat
}

Write-UpdateLog "==== auto-update done ===="
