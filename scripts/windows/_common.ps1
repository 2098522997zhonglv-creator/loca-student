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

    # --noreload: restarts are driven solely by Update-And-Restart.ps1, so a
    # git pull can't kill an in-flight requirement review mid-run.
    $argList = @(
        "backend\manage.py",
        "runserver",
        "$($script:BindHost):$($script:Port)",
        "--noreload"
    )

    $proc = Start-Process `
        -FilePath $script:PythonExe `
        -ArgumentList $argList `
        -WorkingDirectory $script:RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $script:OutLog `
        -RedirectStandardError $script:ErrLog `
        -PassThru

    Set-Content -Path $script:PidFile -Value $proc.Id -Encoding ASCII
    Write-UpdateLog "Started Django PID=$($proc.Id) at http://$($script:BindHost):$($script:Port)/"
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
