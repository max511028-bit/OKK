# Защита от повторного авто-апдейта Ollama (см. lively-floating-cat.md, инцидент 2026-05-16)
# 1) Убираем ярлык Ollama.lnk из автозапуска пользователя — это запускает 'ollama app.exe' (GUI/tray), который и тащит updater
# 2) Блокируем 'ollama app.exe' в Windows Firewall (outbound) — на случай если GUI всё-таки запустят руками, он не сможет скачать новую версию
# 3) Наша SYSTEM-задача STH-Ollama-Serve запускает только 'ollama.exe serve' напрямую — она НЕ обращается к updater'у, GPU-раннер работает штатно.

$ErrorActionPreference = 'Stop'

Write-Output '=== 1. Remove GUI autostart shortcut ==='
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk'
if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Output "removed: $lnk"
} else {
    Write-Output "not present: $lnk"
}

Write-Output '=== 2. Block ollama app.exe in firewall (outbound) ==='
$exe = 'C:\Users\user\AppData\Local\Programs\Ollama\ollama app.exe'
$ruleName = 'STH-Block-Ollama-Updater'
# Удаляем старое правило если есть
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
# Создаём новое — блок outbound для GUI бинарника
New-NetFirewallRule -DisplayName $ruleName -Direction Outbound -Program $exe -Action Block -Profile Any -Description 'Prevent ollama app.exe (tray/updater) from contacting ollama.com for auto-update. STH portal uses SYSTEM-task ollama.exe serve directly.' | Out-Null
Write-Output "firewall rule installed: $ruleName"

Write-Output '=== Done. Summary ==='
Write-Output 'GUI autostart: removed'
Write-Output 'GUI network:   blocked outbound'
Write-Output 'Server:        ollama.exe serve under SYSTEM (STH-Ollama-Serve task) — unaffected'
