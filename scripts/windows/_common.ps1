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

# Boot dependencies for Start-AllServices.ps1; override any of them in config.ps1.
if (-not $script:OllamaPort) {
    $script:OllamaPort = 11434
}
if (-not $script:QdrantContainer) {
    $script:QdrantContainer = "qdrant"
}
if (-not $script:QdrantPort) {
    $script:QdrantPort = 6333
}
if (-not $script:DockerDesktopExe) {
    $script:DockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}
if ($null -eq $script:XinferenceEnabled) {
    $script:XinferenceEnabled = $true
}
if (-not $script:XinferenceEnvDir) {
    $script:XinferenceEnvDir = "D:\conda\envs\xinference"
}
if (-not $script:XinferencePort) {
    $script:XinferencePort = 9998
}
if (-not $script:RerankModel) {
    $script:RerankModel = "bge-reranker-v2-m3"
}
if ($null -eq $script:ActuatorEnabled) {
    $script:ActuatorEnabled = $true
}
if (-not $script:ActuatorDir) {
    $script:ActuatorDir = Join-Path $script:RepoRoot "loca_stude_Actuator"
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

function Get-SavedPythonPid {
    # 重启后 Windows 会复用进程号：只认仍在运行的 python 进程，否则视为过期 PID 文件。
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }
    $saved = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $saved) { return $null }
    $procId = 0
    if (-not [int]::TryParse($saved.Trim(), [ref]$procId)) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.ProcessName -notmatch '^pythonw?$') {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $procId
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
    if ($pids.Count -eq 0) {
        $saved = Get-SavedPythonPid -PidFile $script:PidFile
        if ($saved) { $pids = @($saved) }
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
        "loca_stude_django.asgi:application"
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
    return Get-SavedPythonPid -PidFile $script:WorkerPidFile
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
        "-A", "loca_stude_django",
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
    return Get-SavedPythonPid -PidFile $script:BeatPidFile
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
        Write-UpdateLog "Celery beat already running PID=$existing"
        return
    }

    $argList = @(
        "-m", "celery",
        "-A", "loca_stude_django",
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

function Test-PortOpen {
    param([int]$Port, [string]$HostName = "127.0.0.1")
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        return ($iar.AsyncWaitHandle.WaitOne(1000) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-PortOpen {
    param([int]$Port, [int]$TimeoutSec = 60, [string]$Name = "")
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen -Port $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    Write-UpdateLog "$Name port $Port not ready after ${TimeoutSec}s"
    return $false
}

function Invoke-NativeQuiet {
    # 原生命令写 stderr 时，$ErrorActionPreference=Stop 会把它当异常抛出。
    param([string]$FilePath, [string[]]$Arguments)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @Arguments 2>&1
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output | Out-String).Trim() }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Start-Ollama {
    if (Test-PortOpen -Port $script:OllamaPort) {
        Write-UpdateLog "Ollama already listening on $($script:OllamaPort)"
        return
    }
    $exe = $null
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { $exe = $cmd.Source }
    if (-not $exe) {
        $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $candidate) { $exe = $candidate }
    }
    if (-not $exe) {
        Write-UpdateLog "Ollama not found, skip"
        return
    }
    Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $script:LogDir "ollama.out.log") `
        -RedirectStandardError (Join-Path $script:LogDir "ollama.err.log") | Out-Null
    if (Wait-PortOpen -Port $script:OllamaPort -TimeoutSec 60 -Name "Ollama") {
        Write-UpdateLog "Started Ollama on $($script:OllamaPort)"
    }
}

function Start-QdrantContainer {
    if (Test-PortOpen -Port $script:QdrantPort) {
        Write-UpdateLog "Qdrant already listening on $($script:QdrantPort)"
        return
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-UpdateLog "docker not found, skip Qdrant"
        return
    }
    if ((Invoke-NativeQuiet docker @("info")).ExitCode -ne 0) {
        if (Test-Path $script:DockerDesktopExe) {
            Write-UpdateLog "Docker engine down, launching Docker Desktop..."
            Start-Process -FilePath $script:DockerDesktopExe | Out-Null
        }
        $deadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            if ((Invoke-NativeQuiet docker @("info")).ExitCode -eq 0) { break }
        }
    }
    $result = Invoke-NativeQuiet docker @("start", $script:QdrantContainer)
    if ($result.ExitCode -ne 0) {
        Write-UpdateLog "docker start $($script:QdrantContainer) failed: $($result.Output)"
        return
    }
    if (Wait-PortOpen -Port $script:QdrantPort -TimeoutSec 60 -Name "Qdrant") {
        Write-UpdateLog "Started Qdrant container '$($script:QdrantContainer)'"
    }
}

function Start-XinferenceReranker {
    if (-not $script:XinferenceEnabled) { return }
    $scripts = Join-Path $script:XinferenceEnvDir "Scripts"
    $localExe = Join-Path $scripts "xinference-local.exe"
    $cliExe = Join-Path $scripts "xinference.exe"
    if (-not (Test-Path $localExe)) {
        Write-UpdateLog "Xinference not found at $scripts, skip"
        return
    }
    # 新版默认开启鉴权；本机部署关闭，平台侧 Reranker API 密钥留空。
    $env:XINFERENCE_AUTH_ADVANCED = "false"
    $env:XINFERENCE_MODEL_SRC = "modelscope"
    $endpoint = "http://127.0.0.1:$($script:XinferencePort)"

    if (-not (Test-PortOpen -Port $script:XinferencePort)) {
        Start-Process -FilePath $localExe `
            -ArgumentList @("--host", "127.0.0.1", "--port", "$($script:XinferencePort)") `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $script:LogDir "xinference.out.log") `
            -RedirectStandardError (Join-Path $script:LogDir "xinference.err.log") | Out-Null
        if (-not (Wait-PortOpen -Port $script:XinferencePort -TimeoutSec 180 -Name "Xinference")) {
            return
        }
        Write-UpdateLog "Started Xinference at $endpoint"
    }

    try {
        $models = Invoke-RestMethod -Uri "$endpoint/v1/models" -TimeoutSec 10
        if ($models.data | Where-Object { $_.id -eq $script:RerankModel }) {
            Write-UpdateLog "Rerank model $($script:RerankModel) already loaded"
            return
        }
    } catch {
        Write-UpdateLog "Query Xinference models failed: $($_.Exception.Message)"
    }
    Write-UpdateLog "Launching rerank model $($script:RerankModel)..."
    $result = Invoke-NativeQuiet $cliExe @(
        "launch", "--model-name", $script:RerankModel,
        "--model-type", "rerank", "--endpoint", $endpoint
    )
    if ($result.ExitCode -eq 0) {
        Write-UpdateLog "Rerank model $($script:RerankModel) launched"
    } else {
        Write-UpdateLog "Rerank model launch failed: $($result.Output)"
    }
}

function Start-Actuator {
    if (-not $script:ActuatorEnabled) { return }
    $python = Join-Path $script:ActuatorDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $python) -or -not (Test-Path (Join-Path $script:ActuatorDir "config.toml"))) {
        Write-UpdateLog "Actuator not set up in $($script:ActuatorDir) (run start.bat once), skip"
        return
    }
    $pidFile = Join-Path $script:LogDir "actuator.pid"
    $existing = Get-SavedPythonPid -PidFile $pidFile
    if ($existing) {
        Write-UpdateLog "Actuator already running PID=$existing"
        return
    }
    $proc = Start-Process -FilePath $python `
        -ArgumentList @("main.py", "--id", "actuator-$env:COMPUTERNAME") `
        -WorkingDirectory $script:ActuatorDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $script:LogDir "actuator.out.log") `
        -RedirectStandardError (Join-Path $script:LogDir "actuator.err.log") `
        -PassThru
    Set-Content -Path $pidFile -Value $proc.Id -Encoding ASCII
    Write-UpdateLog "Started actuator PID=$($proc.Id)"
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
