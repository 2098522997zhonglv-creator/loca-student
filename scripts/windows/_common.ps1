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
