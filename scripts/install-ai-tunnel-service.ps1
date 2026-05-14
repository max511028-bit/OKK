# STH · Регистрация SSH-туннеля как scheduled task.
# Запускать ОТ АДМИНА. Один раз.
#
# Использует встроенный Task Scheduler (без сторонних утилит вроде nssm).
# Задача STH-AI-Tunnel:
#   - триггер: при старте Windows (до логина пользователя)
#   - runas: SYSTEM
#   - запускает start-ai-tunnel.ps1, который сам в бесконечном цикле держит ssh
#
# Запуск:
#   powershell -ExecutionPolicy Bypass -File scripts\install-ai-tunnel-service.ps1

$ErrorActionPreference = 'Stop'

function Write-Step($s) { Write-Host "==> $s" -ForegroundColor Cyan }
function Write-Ok($s)   { Write-Host "OK  $s" -ForegroundColor Green }
function Write-Warn($s) { Write-Host "!!! $s" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Запусти от админа!"
    exit 1
}

$repoRoot  = Split-Path -Parent $PSScriptRoot
$tunnelPs1 = Join-Path $repoRoot 'scripts\start-ai-tunnel.ps1'
$taskName  = 'STH-AI-Tunnel'

if (-not (Test-Path $tunnelPs1)) {
    Write-Warn "Не найден $tunnelPs1"
    exit 1
}

# 1. Cloudflared в утиль
Write-Step "Отключаю старый сервис cloudflared (он больше не нужен)..."
try {
    Stop-Service cloudflared -Force -ErrorAction SilentlyContinue
    Set-Service cloudflared -StartupType Disabled -ErrorAction SilentlyContinue
    Write-Ok "cloudflared остановлен и отключён"
} catch { Write-Warn "не удалось отключить cloudflared: $($_.Exception.Message)" }

# 2. Логи
$logDir = 'C:\ProgramData\sth'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# 3. Снести старый таск если был
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Step "Удаляю существующий таск $taskName..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# 4. Регистрируем таск
Write-Step "Регистрирую задачу $taskName..."
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$tunnelPs1`""

$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT15S'  # 15 секунд после старта Windows, чтобы сеть успела подняться

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([System.TimeSpan]::Zero)  # без лимита, задача вечная

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'STH AI Tunnel — SSH reverse tunnel: локальная Ollama (11434) → VPS (21434). Автоперезапуск при обрыве.' `
    -Force | Out-Null
Write-Ok "задача зарегистрирована"

# 5. Запустить сейчас
Write-Step "Запускаю задачу..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "LastTaskResult: $($info.LastTaskResult)   LastRunTime: $($info.LastRunTime)"

# 6. Проверка процесса
Start-Sleep -Seconds 3
$sshProc = Get-Process ssh -ErrorAction SilentlyContinue
if ($sshProc) {
    Write-Ok "ssh.exe запущен (PID $($sshProc.Id -join ','))"
} else {
    Write-Warn "ssh.exe не виден — посмотри лог: Get-Content $logDir\ai-tunnel.log -Tail 30"
}

Write-Host ""
Write-Host "Готово. Лог: $logDir\ai-tunnel.log" -ForegroundColor Green
Write-Host ""
Write-Host "Дальше — Клод:" -ForegroundColor Yellow
Write-Host "  • проверит на VPS, что порт 21434 принимает данные"
Write-Host "  • переключит ai_url.json на http://127.0.0.1:21434"
Write-Host "  • перезапустит tasks-api"
