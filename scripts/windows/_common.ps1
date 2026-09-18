# Shared paths for Windows local auto-update.
# Edit PYTHON_EXE if your conda env path is different.

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:ConfigPath = Join-Path $PSScriptRoot "config.ps1"

if (Test-Path $script:ConfigPath) {
    . $script:ConfigPath
}

if (-not $script:PythonExe) {
    $script:PythonExe = "D:\conda\envs\loca_stude\python.exe"
}
if (-not $script:BindHost) {
    $script:BindHost = "0.0.0.0"
}
if (-not $script:Port) {
    $script:Port = 8000
}
if (-not $script:Branch) {
    $script:Branch = "main"
}
if (-not $script:Remote) {
    $script:Remote = "origin"
}

$script:LogDir = Join-Path $script:RepoRoot "data\logs"
$script:PidFile = Join-Path $script:LogDir "django.pid"
$script:OutLog = Join-Path $script:LogDir "django.out.log"
$script:ErrLog = Join-Path $script:LogDir "django.err.log"
$script:UpdateLog = Join-Path $script:LogDir "auto_update.log"
$script:WorkerPidFile = Join-Path $script:LogDir "celery.pid"
$script:WorkerOutLog = Join-Path $script:LogDir "celery.out.log"
$script:WorkerErrLog = Join-Path $script:LogDir "celery.err.log"
$script:BeatPidFile = Join-Path $script:LogDir "celery-beat.pid"
$script:BeatOutLog = Join-Path $script:LogDir "celery-beat.out.log"
$script:BeatErrLog = Join-Path $script:LogDir "celery-beat.err.log"

function Ensure-LogDir {
    if (-not (Test-Path $script:LogDir)) {
        New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    }
}

function Write-UpdateLog {
    param([string]$Message)
    Ensure-LogDir
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $script:UpdateLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-ListenPids {
    param([int]$Port)
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $conns) { return @() }
        return @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Stop-KnowledgeCenter {
    Ensure-LogDir
    $pids = Get-ListenPids -Port $script:Port
    if ($pids.Count -eq 0 -and (Test-Path $script:PidFile)) {
        $saved = Get-Content $script:PidFile -ErrorAction SilentlyContinue
        if ($saved) { $pids = @([int]$saved) }
    }
    foreach ($procId in $pids) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-UpdateLog "Stopped process PID=$procId on port $($script:Port)"
        } catch {
            Write-UpdateLog "Stop PID=$procId failed: $($_.Exception.Message)"
        }
    }
    if (Test-Path $script:PidFile) {
        Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

function Start-KnowledgeCenter {
    Ensure-LogDir
    if (-not (Test-Path $script:PythonExe)) {
        throw "Python not found: $script:PythonExe  Edit scripts\windows\config.ps1"
    }

    $listening = Get-ListenPids -Port $script:Port
    if ($listening.Count -gt 0) {
        Write-UpdateLog "Already listening on $($script:Port), PIDs=$($listening -join ',')"
        return
    }

    # UI 自动化需要 WebSocket，必须用 ASGI（daphne）。
    # 工作目录切到 backend，保证 Django settings / asgi 模块可导入。
    $argList = @(
        "-m", "daphne",
        "-b", $script:BindHost,
        "-p", "$($script:Port)",
        "wharttest_django.asgi:application"
    )

    $proc = Start-Process `
        -FilePath $script:PythonExe `
        -ArgumentList $argList `
        -WorkingDirectory (Join-Path $script:RepoRoot "backend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput $script:OutLog `
        -RedirectStandardError $script:ErrLog `
        -PassThru

    Set-Content -Path $script:PidFile -Value $proc.Id -Encoding ASCII
    Write-UpdateLog "Started Daphne PID=$($proc.Id) at http://$($script:BindHost):$($script:Port)/"
}

function Get-WorkerPid {
    if (-not (Test-Path $script:WorkerPidFile)) { return $null }
    $saved = Get-Content $script:WorkerPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $saved) { return $null }
    $procId = 0
    if (-not [int]::TryParse($saved.Trim(), [ref]$procId)) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    return $procId
}

function Stop-ReviewWorker {
    Ensure-LogDir
    $procId = Get-WorkerPid
    if ($procId) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-UpdateLog "Stopped celery worker PID=$procId"
        } catch {
            Write-UpdateLog "Stop celery worker PID=$procId failed: $($_.Exception.Message)"
        }
    }
    if (Test-Path $script:WorkerPidFile) {
        Remove-Item $script:WorkerPidFile -Force -ErrorAction SilentlyContinue
    }
}

function Start-ReviewWorker {
    Ensure-LogDir

    $existing = Get-WorkerPid
    if ($existing) {
        Write-UpdateLog "Celery worker already running PID=$existing"
        return
    }

    # -P solo：Windows 不支持 prefork。评审内部已用线程池并发，单任务串行即可。
    $argList = @(
        "-m", "celery",
        "-A", "wharttest_django",
        "worker",
        "-l", "info",
        "-P", "solo"
    )

    $proc = Start-Process `
        -FilePath $script:PythonExe `
        -ArgumentList $argList `
        -WorkingDirectory (Join-Path $script:RepoRoot "backend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput $script:WorkerOutLog `
        -RedirectStandardError $script:WorkerErrLog `
        -PassThru

    Set-Content -Path $script:WorkerPidFile -Value $proc.Id -Encoding ASCII
    Write-UpdateLog "Started celery worker PID=$($proc.Id)"
}

function Get-BeatPid {
    if (-not (Test-Path $script:BeatPidFile)) { return $null }
    $raw = (Get-Content $script:BeatPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $raw) { return $null }
    try { return [int]$raw } catch { return $null }
}

function Stop-CeleryBeat {
    Ensure-LogDir
    $procId = Get-BeatPid
    if ($procId) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-UpdateLog "Stopped celery beat PID=$procId"
        } catch {
            Write-UpdateLog "Stop celery beat PID=$procId failed: $($_.Exception.Message)"
        }
    }
    if (Test-Path $script:BeatPidFile) {
        Remove-Item $script:BeatPidFile -Force -ErrorAction SilentlyContinue
    }
}

function Start-CeleryBeat {
    Ensure-LogDir

    $existing = Get-BeatPid
    if ($existing) {
        $alive = Get-Process -Id $existing -ErrorAction SilentlyContinue
        if ($alive) {
            Write-UpdateLog "Celery beat already running PID=$existing"
            return
        }
    }

    $argList = @(
        "-m", "celery",
        "-A", "wharttest_django",
        "beat",
        "-l", "info"
    )

    $proc = Start-Process `
        -FilePath $script:PythonExe `
        -ArgumentList $argList `
        -WorkingDirectory (Join-Path $script:RepoRoot "backend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput $script:BeatOutLog `
        -RedirectStandardError $script:BeatErrLog `
        -PassThru

    Set-Content -Path $script:BeatPidFile -Value $proc.Id -Encoding ASCII
    Write-UpdateLog "Started celery beat PID=$($proc.Id)"
}

function Sync-BundledSkills {
    # 每次调用都安全：内部按 commit 打标记，同一版本只同步一次。
    # 不依赖调用方判断是否有代码变更，避免漏掉"无更新但从未同步"的情况。
    param([switch]$Force)

    Ensure-LogDir
    $skillsDir = Join-Path $script:RepoRoot "bundled_skills"
    if (-not (Test-Path $skillsDir)) {
        Write-UpdateLog "bundled_skills not found, skip skill sync"
        return
    }

    $stampFile = Join-Path $script:LogDir "skills_synced.commit"
    $head = ""
    try {
        $head = (git -C $script:RepoRoot rev-parse HEAD 2>$null).Trim()
    } catch {
        $head = ""
    }

    if (-not $Force -and $head -and (Test-Path $stampFile)) {
        $synced = Get-Content $stampFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($synced -and $synced.Trim() -eq $head) {
            return
        }
    }

    Push-Location $script:RepoRoot
    # Django 的日志走 stderr，配合 2>&1 会在 $ErrorActionPreference=Stop 下被
    # 当成 NativeCommandError 抛出，导致成功的命令被判为失败。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Write-UpdateLog "Syncing bundled skills..."
        $output = & $script:PythonExe "backend\manage.py" init_skills --skills-dir $skillsDir 2>&1
        foreach ($line in $output) { Write-UpdateLog "  $line" }
        if ($LASTEXITCODE -eq 0) {
            # 仅成功后记录，失败则下轮自动重试。
            if ($head) { Set-Content -Path $stampFile -Value $head -Encoding ASCII }
        } else {
            # Skill 同步失败不应阻断服务启动，记录后继续。
            Write-UpdateLog "init_skills failed (exit=$LASTEXITCODE), continuing"
        }
    } catch {
        Write-UpdateLog "init_skills error: $($_.Exception.Message)"
    } finally {
        $ErrorActionPreference = $prevEap
        Pop-Location
    }
}

function Invoke-FrontendBuild {
    Push-Location $script:RepoRoot
    try {
        Write-UpdateLog "Building frontend..."
        npm --prefix frontend install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        npm --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Write-UpdateLog "Frontend build finished"
    } finally {
        Pop-Location
    }
}
