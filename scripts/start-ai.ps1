
# STH AI Launcher
# Запускает Ollama + Cloudflare-туннель и публикует адрес на портале.

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

$VPS = "http://195.208.119.67/tasks/api/ai/url"

Write-Host ""
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host "         STH AI Launcher               " -ForegroundColor Cyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""

# -- Найти cloudflared.exe --
$cf = $null
$candidates = @(
    "$PSScriptRoot\..\cloudflared-windows-amd64.exe",
    "$PSScriptRoot\..\cloudflared.exe",
    "$PSScriptRoot\cloudflared-windows-amd64.exe",
    "$PSScriptRoot\cloudflared.exe",
    "$env:USERPROFILE\Downloads\cloudflared-windows-amd64.exe",
    "$env:USERPROFILE\Downloads\cloudflared.exe",
    "C:\cloudflared\cloudflared.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) {
        $cf = (Resolve-Path $c).Path
        break
    }
}
if (-not $cf) {
    $inPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($inPath) { $cf = $inPath.Source }
}

if (-not $cf) {
    Write-Host "  [ERR] cloudflared.exe не найден" -ForegroundColor Red
    Write-Host "  Положите cloudflared-windows-amd64.exe рядом с start-ai.bat" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
Write-Host "  [OK]  cloudflared: $cf" -ForegroundColor Green

# -- Запустить Ollama --
Write-Host "  ...   Проверяю Ollama..." -ForegroundColor Yellow
$ollamaRunning = $false
try {
    Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
    $ollamaRunning = $true
    Write-Host "  [OK]  Ollama уже запущена" -ForegroundColor Green
} catch {
    $env:OLLAMA_ORIGINS = "*"
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Write-Host "  ...   Жду запуска Ollama" -NoNewline -ForegroundColor Yellow
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
        Write-Host "  [OK]  Ollama запущена" -ForegroundColor Green
    } else {
        Write-Host "  [!!]  Ollama не ответила - продолжаю" -ForegroundColor Yellow
    }
}

$env:OLLAMA_ORIGINS = "*"

# -- Запустить туннель --
Write-Host "  ...   Запускаю туннель Cloudflare..." -ForegroundColor Yellow
$logFile = "$env:TEMP\sth_cf_$PID.log"

$cfProc = Start-Process -FilePath $cf `
    -ArgumentList "tunnel --url http://localhost:11434" `
    -RedirectStandardError $logFile `
    -PassThru -WindowStyle Hidden

Write-Host "  ...   Жду адрес туннеля" -NoNewline -ForegroundColor Yellow
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

if (-not $tunnelUrl) {
    Write-Host "  [ERR] Не удалось получить адрес туннеля" -ForegroundColor Red
    if ($cfProc -and -not $cfProc.HasExited) { $cfProc.Kill() }
    Start-Sleep -Seconds 5
    exit 1
}

Write-Host "  [OK]  Туннель: $tunnelUrl" -ForegroundColor Green

# -- Отправить URL на VPS --
Write-Host "  ...   Публикую адрес на портале..." -ForegroundColor Yellow
try {
    $body = '{"url":"' + $tunnelUrl + '"}'
    Invoke-RestMethod -Uri $VPS -Method POST -Body $body -ContentType "application/json" -ErrorAction Stop | Out-Null
    Write-Host "  [OK]  Адрес опубликован" -ForegroundColor Green
} catch {
    Write-Host "  [!!]  Не удалось опубликовать: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host "  ИИ доступен для всех пользователей!" -ForegroundColor Green
Write-Host "  Туннель: $tunnelUrl" -ForegroundColor White
Write-Host "  Портал:  http://195.208.119.67" -ForegroundColor White
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Закройте это окно чтобы выключить ИИ" -ForegroundColor Yellow
Write-Host ""

# -- Держать живым пока туннель работает --
try {
    $cfProc | Wait-Process -ErrorAction SilentlyContinue
} catch {}

# -- Очистить URL на сервере --
Write-Host ""
Write-Host "  Выключаю ИИ для всех пользователей..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri $VPS -Method POST -Body '{"url":null}' -ContentType "application/json" -ErrorAction SilentlyContinue | Out-Null
} catch {}

if (Test-Path $logFile) { Remove-Item $logFile -Force -ErrorAction SilentlyContinue }

Write-Host "  Готово. ИИ выключен." -ForegroundColor Green
Start-Sleep -Seconds 2
