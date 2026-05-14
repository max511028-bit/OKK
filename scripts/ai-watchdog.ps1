# STH AI Watchdog v3 — мониторим SSH-туннель (а не Cloudflare).
# Раз в минуту:
#   1) проверяем локальную Ollama (http://localhost:11434/api/tags)
#   2) проверяем "через VPS" — https://portalsth.ru/tasks/api/ai/health
#      (бэкенд на VPS ходит в Ollama по SSH-туннелю → 127.0.0.1:21434).
# Если внешний путь падает → перезапускаем scheduled task STH-AI-Tunnel
# (она держит ssh -R reverse forward).
# Если локальный Ollama падает → перезапускаем ollama.

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$logDir = 'C:\ProgramData\sth'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir 'ai-watchdog.log'
$stateFile = Join-Path $logDir 'ai-watchdog.state'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$ts  $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

# Состояние: счётчики отказов
$state = @{ ollama = 0; tunnel = 0 }
if (Test-Path $stateFile) {
    try {
        $loaded = Get-Content $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json
        if ($loaded.ollama) { $state.ollama = [int]$loaded.ollama }
        if ($loaded.tunnel) { $state.tunnel = [int]$loaded.tunnel }
    } catch {}
}

function Test-Endpoint($url, $name) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        if ($resp.StatusCode -eq 200) { return $true }
        Write-Log "$name HTTP $($resp.StatusCode)"
        return $false
    } catch {
        Write-Log "$name fail: $($_.Exception.Message)"
        return $false
    }
}

# 1. Локальная Ollama
$ollamaOk = Test-Endpoint 'http://localhost:11434/api/tags' 'ollama'
if ($ollamaOk) {
    if ($state.ollama -gt 0) { Write-Log "ollama recovered after $($state.ollama) fails" }
    $state.ollama = 0
} else {
    $state.ollama++
    if ($state.ollama -ge 2) {
        Write-Log "RESTARTING ollama (fails=$($state.ollama))"
        try {
            Get-Process -Name 'ollama' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
            Write-Log "ollama started"
        } catch { Write-Log "ollama restart error: $($_.Exception.Message)" }
        $state.ollama = 0
    }
}

# 2. Внешний путь — через VPS-бэкенд, который ходит в нашу Ollama по SSH-туннелю.
#    Проверяем /ai/health (он кэшируется на VPS 30с, но это окей).
#    Делаем это только если локальная Ollama жива — иначе бессмысленно дёргать туннель.
if ($ollamaOk) {
    $tunnelOk = Test-Endpoint 'https://portalsth.ru/tasks/api/ai/health' 'tunnel'
    # /ai/health отвечает 200 с {"online": true|false}. Парсим JSON чтоб различить.
    if ($tunnelOk) {
        try {
            $data = Invoke-RestMethod -Uri 'https://portalsth.ru/tasks/api/ai/health' -TimeoutSec 10
            if (-not $data.online) {
                Write-Log "tunnel health=offline reason=$($data.reason)"
                $tunnelOk = $false
            }
        } catch { $tunnelOk = $false }
    }

    if ($tunnelOk) {
        if ($state.tunnel -gt 0) { Write-Log "tunnel recovered after $($state.tunnel) fails" }
        $state.tunnel = 0
    } else {
        $state.tunnel++
        if ($state.tunnel -ge 2) {
            Write-Log "RESTARTING SSH tunnel (fails=$($state.tunnel))"
            # Перезапуск scheduled task STH-AI-Tunnel: stop, kill ssh, start.
            try {
                Stop-ScheduledTask -TaskName 'STH-AI-Tunnel' -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Get-Process -Name 'ssh' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Start-ScheduledTask -TaskName 'STH-AI-Tunnel'
                Write-Log "SSH tunnel restart triggered"
            } catch { Write-Log "tunnel restart error: $($_.Exception.Message)" }
            $state.tunnel = 0
        }
    }
}

# Сохранить состояние
$state | ConvertTo-Json -Compress | Out-File -FilePath $stateFile -Encoding utf8

# Ротация лога — последние 1500 строк
try {
    if (Test-Path $logFile) {
        $lines = Get-Content $logFile -Tail 1500
        $lines | Out-File -FilePath $logFile -Encoding utf8
    }
} catch {}
