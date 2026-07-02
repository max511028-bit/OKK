# STH · регистрация scheduled task STH-Dispatch-Agent
# - триггер: AtLogon (для текущего юзера)
# - запуск под текущим пользователем (НЕ SYSTEM) — нужен доступ к
#   C:\ProgramData\sth\voicecall.env и реальной сетевой карте для SIP/RTP
# - держит voicecall/dispatch_agent.py запущенным постоянно, с
#   автоперезапуском при падении (см. run-dispatch-agent.ps1)

$ErrorActionPreference = 'Stop'

$logDir = 'C:\ProgramData\sth'
if (-not (Test-Path $logDir)) {
    try { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    catch { $logDir = $env:TEMP }
}
try { Start-Transcript -Path (Join-Path $logDir 'install-dispatch-agent.log') -Force | Out-Null } catch {}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "isAdmin: $isAdmin"

$TaskName = 'STH-Dispatch-Agent'
$Script   = 'C:\Users\user\Documents\КЛод\ОКК\OKK\scripts\run-dispatch-agent.ps1'

if (-not (Test-Path $Script)) { throw "wrapper script not found: $Script" }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Пересоздаю $TaskName"
    Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""

# Один триггер — при логине. dispatch_agent.py сам по себе бесконечный
# цикл (в отличие от watchdog'а, который делает разовую проверку и
# выходит), повторный запуск по расписанию не нужен — только на логин
# плюс автоперезапуск при падении (см. RestartCount ниже и сама
# обёртка run-dispatch-agent.ps1).
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $user

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 9999) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$runLevel = if ($isAdmin) { 'Highest' } else { 'Limited' }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel $runLevel

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $tLogon `
    -Settings $settings `
    -Principal $principal `
    -Force `
    -Description 'STH · держит voicecall/dispatch_agent.py запущенным постоянно (обзвон кандидатов по SIP-линии). AtLogon, автоперезапуск при падении.' | Out-Null

Write-Host "✅ Таск $TaskName зарегистрирован для $user (AtLogon, RunLevel=$runLevel)." -ForegroundColor Green
Write-Host ""
Write-Host "Запускаю первый раз прямо сейчас..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object LastRunTime,LastTaskResult,NextRunTime | Format-Table -AutoSize
Write-Host ""
Write-Host "Log: C:\ProgramData\sth\dispatch-agent.log"
try { Stop-Transcript | Out-Null } catch {}
