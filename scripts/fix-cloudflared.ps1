# STH · Починка туннеля Cloudflare (запускать ОТ АДМИНА).
# Скрипт:
#   1) Останавливает сервис cloudflared
#   2) Скачивает свежую версию (если в системе старая)
#   3) Подменяет cloudflared.exe
#   4) Запускает сервис
#   5) Проверяет внешний эндпоинт https://ai.portalsth.ru/api/tags
#
# Запуск: правой кнопкой → Run with PowerShell as administrator
#         либо в админ-консоли:
#         pwsh -ExecutionPolicy Bypass -File scripts\fix-cloudflared.ps1

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step($s) { Write-Host "==> $s" -ForegroundColor Cyan }
function Write-Ok($s)   { Write-Host "OK  $s" -ForegroundColor Green }
function Write-Warn($s) { Write-Host "!!! $s" -ForegroundColor Yellow }

# 0. Проверка прав
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Запусти скрипт от админа! (правый клик → Run as administrator)"
    exit 1
}

$exePath = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$exePath64 = 'C:\Program Files\cloudflared\cloudflared.exe'
if (Test-Path $exePath64) { $exePath = $exePath64 }
Write-Step "cloudflared.exe path: $exePath"

# 1. Текущая версия
$curVer = 'unknown'
try { $curVer = (& $exePath --version 2>&1) -join ' ' } catch {}
Write-Host "Текущая версия: $curVer"

# 2. Остановить сервис
Write-Step "Останавливаю сервис cloudflared..."
try { Stop-Service -Name 'cloudflared' -Force -ErrorAction Stop; Write-Ok "сервис остановлен" }
catch { Write-Warn "не удалось остановить сервис: $($_.Exception.Message)" }
# Подождать пока процесс умрёт
for ($i = 0; $i -lt 10; $i++) {
    if (-not (Get-Process cloudflared -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Seconds 1
}
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 3. Скачать свежую версию
$tmp = Join-Path $env:TEMP 'cloudflared-new.exe'
Write-Step "Скачиваю свежий cloudflared.exe..."
$url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
Write-Ok "скачано в $tmp"

# 4. Подменить бинарник
Write-Step "Подменяю $exePath ..."
Copy-Item -Path $tmp -Destination $exePath -Force
Write-Ok "файл заменён"
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

# 5. Проверить новую версию
$newVer = (& $exePath --version 2>&1) -join ' '
Write-Host "Новая версия: $newVer"

# 6. Стартовать сервис
Write-Step "Запускаю сервис cloudflared..."
Start-Service -Name 'cloudflared'
Start-Sleep -Seconds 8
$svc = Get-Service cloudflared
Write-Host "Service status: $($svc.Status)"

# 7. Проверить подключение к edge
Write-Step "Проверяю подключение к Cloudflare edge..."
Start-Sleep -Seconds 4
$info = (& $exePath --config 'C:\ProgramData\cloudflared\config.yml' tunnel info e63f2e53-a4cb-4988-8a17-1dbdba5986a4 2>&1) -join "`n"
Write-Host $info

# 8. Внешний smoke
Write-Step "Smoke-test https://ai.portalsth.ru/api/tags ..."
try {
    $r = Invoke-WebRequest -Uri 'https://ai.portalsth.ru/api/tags' -UseBasicParsing -TimeoutSec 15
    if ($r.StatusCode -eq 200) {
        Write-Ok "tunnel ALIVE (HTTP 200)"
        Write-Host ($r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))
    } else {
        Write-Warn "HTTP $($r.StatusCode)"
    }
} catch {
    Write-Warn "smoke fail: $($_.Exception.Message)"
}

Write-Step "Готово. Если smoke ALIVE — на портале плашка «ИИ оффлайн» пропадёт в течение 30 сек."
