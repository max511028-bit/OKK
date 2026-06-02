# STH - re-register STH-Ollama-Serve to run as SYSTEM at Windows startup.
# Currently the task starts only on user logon -> after reboot without autologon
# Ollama does not start and the whole AI pipeline is dead.
#
# What we do:
#   - trigger: AtStartup (before logon)
#   - runas:   SYSTEM
#   - env OLLAMA_MODELS points to user's models (SYSTEM cannot see them otherwise)
#
# Run as admin:
#   powershell -ExecutionPolicy Bypass -File scripts\install-ollama-startup.ps1

$ErrorActionPreference = 'Stop'

function Write-Step($s) { Write-Host "==> $s" -ForegroundColor Cyan }
function Write-Ok($s)   { Write-Host "OK  $s" -ForegroundColor Green }
function Write-Warn($s) { Write-Host "!!! $s" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Run as admin!"
    exit 1
}

$taskName = 'STH-Ollama-Serve'

# 1. Find ollama.exe and models
$ollamaExe = @(
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    'C:\Users\user\AppData\Local\Programs\Ollama\ollama.exe',
    'C:\Program Files\Ollama\ollama.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $ollamaExe) { Write-Warn "ollama.exe not found"; exit 1 }
Write-Ok "ollama.exe = $ollamaExe"

$modelsDir = 'C:\Users\user\.ollama\models'
if (-not (Test-Path $modelsDir)) { Write-Warn "models not found at $modelsDir - check path" }

# 2. Log directory
$logDir = 'C:\ProgramData\sth'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# 3. Wrapper script that sets env and starts ollama serve
$wrapperPath = Join-Path $logDir 'ollama-startup.ps1'
$wrapper = @'
$ErrorActionPreference = 'Continue'
$logDir = 'C:\ProgramData\sth'
$log = Join-Path $logDir 'ollama-serve.log'
$env:OLLAMA_MODELS = 'C:\Users\user\.ollama\models'
$env:OLLAMA_HOST   = '127.0.0.1:11434'
$env:OLLAMA_KEEP_ALIVE = '24h'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"$ts  ollama-startup wrapper started, models=$env:OLLAMA_MODELS" | Out-File -FilePath $log -Append -Encoding utf8
& 'OLLAMA_PATH_PLACEHOLDER' serve *>> $log
'@
$wrapper = $wrapper.Replace('OLLAMA_PATH_PLACEHOLDER', $ollamaExe)
$wrapper | Out-File -FilePath $wrapperPath -Encoding utf8 -Force
Write-Ok "wrapper: $wrapperPath"

# 4. Remove old task
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Step "Removing existing $taskName..."
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Kill any hanging ollama.exe from previous config
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 5. Register
Write-Step "Registering $taskName (SYSTEM, AtStartup)..."
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$wrapperPath`""

$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT10S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([System.TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'STH Ollama Serve - run Ollama as SYSTEM at Windows startup with user models.' `
    -Force | Out-Null
Write-Ok "task registered"

# 6. Start
Write-Step "Starting $taskName..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8

$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "LastTaskResult: $($info.LastTaskResult)   LastRunTime: $($info.LastRunTime)"

# 7. Check
Start-Sleep -Seconds 5
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10 -UseBasicParsing
    if ($r.StatusCode -eq 200) {
        Write-Ok "Ollama responds on 11434, models visible"
    } else {
        Write-Warn "Ollama returned $($r.StatusCode)"
    }
} catch {
    Write-Warn "Ollama not responding: $($_.Exception.Message)"
    Write-Host "Log: Get-Content C:\ProgramData\sth\ollama-serve.log -Tail 30"
}

Write-Host ""
Write-Host "Done. After reboot Ollama will start before user logon." -ForegroundColor Green
