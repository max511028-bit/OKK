# STH · Регистрация ai-watchdog как scheduled task (каждую минуту).
# Запускать ОТ АДМИНА. Один раз.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-ai-watchdog.ps1

$ErrorActionPreference = 'Stop'

function Write-Step($s) { Write-Host "==> $s" -ForegroundColor Cyan }
function Write-Ok($s)   { Write-Host "OK  $s" -ForegroundColor Green }
function Write-Warn($s) { Write-Host "!!! $s" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Запусти от админа!"
    exit 1
}

$repoRoot     = Split-Path -Parent $PSScriptRoot
$watchdogPs1  = Join-Path $repoRoot 'scripts\ai-watchdog.ps1'
$taskName     = 'STH-AI-Watchdog'

if (-not (Test-Path $watchdogPs1)) { Write-Warn "Не найден $watchdogPs1"; exit 1 }

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Step "Удаляю существующий таск $taskName..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Write-Step "Регистрирую $taskName..."
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$watchdogPs1`""

# Раз в минуту. Длительность 10000 дней (~27 лет) — Task Scheduler не принимает
# [TimeSpan]::MaxValue, выдаёт ошибку "значение за пределами допустимого диапазона".
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 10000)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'STH AI Watchdog v3 — раз в минуту проверяет Ollama (локально) и AI-туннель (через portalsth.ru/ai/health). При обрыве перезапускает Ollama / SSH-туннель.' `
    -Force | Out-Null

Write-Ok "$taskName зарегистрирован"

Write-Step "Первый запуск..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 5
Write-Host ""
Write-Host "Готово. Логи: C:\ProgramData\sth\ai-watchdog.log" -ForegroundColor Green
Get-Content C:\ProgramData\sth\ai-watchdog.log -Tail 10 -ErrorAction SilentlyContinue
