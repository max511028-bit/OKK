# STH AI Launcher
# Запускает Ollama + Cloudflare-туннель и публикует адрес на портале.
# Все пользователи портала получают доступ к ИИ пока это окно открыто.

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

$VPS = "http://195.208.119.67/tasks/api/ai/url"

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║        STH AI Launcher  🤖           ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Найти cloudflared.exe ──────────────────────────────────────────────────────
$cf = $null
$candidates = @(
    "$PSScriptRoot\cloudflared.exe",
    "$PSScriptRoot\..\cloudflared.exe",
    "$env:USERPROFILE\Downloads\cloudflared-windows-amd64.exe",
    "$env:USERPROFILE\Downloads\cloudflared.exe",
    "C:\cloudflared\cloudflared.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $cf = (Resolve-Path $c).Path; break }
}
if (!$cf) {
    $inPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($inPath) { $cf = $inPath.Source }
}

if (!$cf) {
    Write-Host "  ❌ Не найден cloudflared.exe" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Положите cloudflared.exe в одну папку с этим файлом:" -ForegroundColor Yellow
    Write-Host "  $PSScriptRoot\" -ForegroundColor White
    Write-Host ""
    Write-Host "  Скачать: https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -ForegroundColor Gray
    Write-Host ""
    Read-Host "  Нажмите Enter для выхода"
    exit 1
}
Write-Host "  ✅ cloudflared найден" -ForegroundColor Green

# ── Запустить Ollama ───────────────────────────────────────────────────────────
Write-Host "  🚀 Проверяю Ollama..." -ForegroundColor Yellow
$ollamaRunning = $false
try {
    Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
    $ollamaRunning = $true
    Write-Host "  ✅ Ollama уже запущена" -ForegroundColor Green
} catch {}

if (!$ollamaRunning) {
    $env:OLLAMA_ORIGINS = "*"
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Write-Host "  ⏳ Ожидаю запуска Ollama" -NoNewline -ForegroundColor Yellow
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        Write-Host "." -NoNewline -ForegroundColor Yellow
        try {
            Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 1 -ErrorAction Stop | Out-Null
            $ollamaRunning = $true
            break
        } catch {}
    }
    Write-Host ""
    if ($ollamaRunning) {
        Write-Host "  ✅ Ollama запущена" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  Ollama не ответила — продолжаю всё равно" -ForegroundColor Yellow
    }
}

# Убедиться что OLLAMA_ORIGINS разрешает внешние запросы
$env:OLLAMA_ORIGINS = "*"

# ── Запустить туннель и захватить URL ─────────────────────────────────────────
Write-Host "  🌐 Запускаю туннель Cloudflare..." -ForegroundColor Yellow
$logFile = "$env:TEMP\sth_cf_$([System.Diagnostics.Process]::GetCurrentProcess().Id).log"

$cfProc = Start-Process -FilePath $cf `
    -ArgumentList "tunnel --url http://localhost:11434" `
    -RedirectStandardError $logFile `
    -PassThru -WindowStyle Hidden

Write-Host "  ⏳ Ожидаю адрес туннеля" -NoNewline -ForegroundColor Yellow
$tunnelUrl = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline -ForegroundColor Yellow
    if (Test-Path $logFile) {
        $log = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        if ($log -match 'https://[a-z0-9\-]+\.trycloudflare\.com') {
            $tunnelUrl = $Matches[0]
            break
        }
    }
}
Write-Host ""

if (!$tunnelUrl) {
    Write-Host "  ❌ Не удалось получить адрес туннеля" -ForegroundColor Red
    Write-Host "  Проверьте интернет-соединение и повторите попытку" -ForegroundColor Yellow
    if ($cfProc -and !$cfProc.HasExited) { $cfProc.Kill() }
    Read-Host "  Нажмите Enter для выхода"
    exit 1
}

Write-Host "  ✅ Туннель активен: $tunnelUrl" -ForegroundColor Green

# ── Отправить URL на VPS ───────────────────────────────────────────────────────
Write-Host "  📡 Публикую адрес на портале..." -ForegroundColor Yellow
try {
    $body = "{`"url`":`"$tunnelUrl`"}"
    Invoke-RestMethod -Uri $VPS -Method POST -Body $body -ContentType "application/json" -ErrorAction Stop | Out-Null
    Write-Host "  ✅ Адрес опубликован" -ForegroundColor Green
} catch {
    Write-Host "  ⚠️  Не удалось опубликовать адрес: $_" -ForegroundColor Yellow
    Write-Host "  ИИ будет работать только на вашем браузере (через localhost)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  🤖 ИИ доступен для всех пользователей!" -ForegroundColor Green
Write-Host "  🔗 Туннель: $tunnelUrl" -ForegroundColor White
Write-Host "  🌍 Портал:  http://195.208.119.67" -ForegroundColor White
Write-Host "  ════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Закройте это окно чтобы выключить ИИ для всех" -ForegroundColor Yellow
Write-Host ""

# ── Держать скрипт живым пока туннель работает ────────────────────────────────
try {
    $cfProc | Wait-Process -ErrorAction SilentlyContinue
} catch {}

# ── Очистить URL на сервере ────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ⏹  Выключаю ИИ для всех пользователей..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri $VPS -Method POST -Body '{"url":null}' -ContentType "application/json" -ErrorAction SilentlyContinue | Out-Null
} catch {}

if (Test-Path $logFile) { Remove-Item $logFile -Force -ErrorAction SilentlyContinue }

Write-Host "  ✅ Готово. ИИ выключен." -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 2
