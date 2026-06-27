# STH · AI watchdog — проверяет и при необходимости поднимает связку:
#   Ollama (локально :11434) → SSH-туннель → /ai/health на VPS.
# Запускается scheduled task'ом STH-AI-Watchdog: при логине + каждый час.
# Запускается под текущим пользователем, не под SYSTEM — иначе CUDA/модели Ollama
# работают через раз.

$ErrorActionPreference = 'Continue'
$ProgressPreference   = 'SilentlyContinue'

$Root      = 'C:\ProgramData\sth'
$LogFile   = Join-Path $Root 'ai-watchdog.log'
$StateFile = Join-Path $Root 'ai-watchdog.state'   # последнее состояние (OK/FAIL) — чтобы не спамить toast'ами
$TunnelScript = 'C:\Users\user\Documents\КЛод\ОКК\OKK\scripts\start-ai-tunnel.ps1'
$OllamaExe = 'C:\Users\user\AppData\Local\Programs\Ollama\ollama.exe'
$VpsHealthUrl = 'https://portalsth.ru/tasks/api/ai/health'

if (-not (Test-Path $Root)) { New-Item -ItemType Directory -Path $Root -Force | Out-Null }

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$ts  $msg" | Out-File -FilePath $LogFile -Append -Encoding utf8
}

function Show-Toast($title, $body, $isError) {
    # Toast через WinRT API — без сторонних модулей. Win10+/Win11.
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName('text')
        $textNodes.Item(0).AppendChild($template.CreateTextNode($title)) | Out-Null
        $textNodes.Item(1).AppendChild($template.CreateTextNode($body))  | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        $appId = 'STH.AI.Watchdog'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    } catch {
        Write-Log "toast failed: $($_.Exception.Message)"
    }
}

function Test-OllamaLocal {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Start-OllamaIfNeeded {
    if (Get-Process ollama -ErrorAction SilentlyContinue) {
        if (Test-OllamaLocal) { return $true }
        Write-Log "ollama process exists but API down — killing and restarting"
        Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Path $OllamaExe)) {
        Write-Log "ollama.exe NOT FOUND at $OllamaExe"
        return $false
    }
    Write-Log "starting ollama serve (KEEP_ALIVE=30m, MODELS=user)"
    $env:OLLAMA_HOST        = '127.0.0.1:11434'
    $env:OLLAMA_KEEP_ALIVE  = '30m'
    $env:OLLAMA_MODELS      = 'C:\Users\user\.ollama\models'
    Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root 'ollama-serve.out.log') `
        -RedirectStandardError  (Join-Path $Root 'ollama-serve.err.log')
    # ждём пока API ответит, максимум 30 сек
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-OllamaLocal) { Write-Log "ollama is up (after $((($i+1)*2)) s)"; return $true }
    }
    Write-Log "ollama did not come up within 30s"
    return $false
}

function Test-SileroLocal {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5001/health' -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -ne 200) { return $false }
        $h = $r.Content | ConvertFrom-Json
        return [bool]$h.model_loaded
    } catch { return $false }
}

function Start-SileroIfNeeded {
    $venvPython = 'C:\ProgramData\sth\silero-venv\Scripts\python.exe'
    $script     = 'C:\Users\user\Documents\КЛод\ОКК\OKK\voicecall\silero_server.py'
    if (-not (Test-Path $venvPython)) {
        Write-Log "silero: venv not found at $venvPython (skip)"
        return $false
    }
    if (-not (Test-Path $script)) {
        Write-Log "silero: script not found at $script (skip)"
        return $false
    }
    # Если уже бежит — проверяем что модель загружена
    if (Test-SileroLocal) { return $true }
    # Прибиваем зомби-процессы silero_server
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | `
        Where-Object { $_.CommandLine -like '*silero_server*' } | ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
    Write-Log "starting silero_server.py"
    Start-Process -FilePath $venvPython -ArgumentList $script -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root 'silero-server.log') `
        -RedirectStandardError  (Join-Path $Root 'silero-server.err.log')
    # Ждём до 30с пока модель загрузится
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-SileroLocal) { Write-Log "silero is up (after $((($i+1)*2))s)"; return $true }
    }
    Write-Log "silero did not come up within 30s"
    return $false
}

function Test-TunnelRunning {
    # Признак живого туннеля: есть PowerShell-процесс, в котором крутится start-ai-tunnel.ps1.
    # Простой способ — посмотреть, есть ли ssh-процесс, запущенный за последний час.
    $ssh = Get-Process ssh -ErrorAction SilentlyContinue
    if (-not $ssh) { return $false }
    # Любой ssh.exe не годится — проверим, есть ли соединение на 127.0.0.1:11434 со стороны ssh
    try {
        $conns = Get-NetTCPConnection -RemoteAddress '127.0.0.1' -RemotePort 11434 -State Established -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($ssh.Id -contains $c.OwningProcess) { return $true }
        }
    } catch {}
    # fallback — если ssh.exe хоть один и стартовал недавно, считаем живым
    return ($ssh | Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-65) }).Count -gt 0
}

function Start-TunnelIfNeeded {
    if (Test-TunnelRunning) { return $true }
    Write-Log "tunnel not running — launching $TunnelScript"
    if (-not (Test-Path $TunnelScript)) {
        Write-Log "tunnel script NOT FOUND: $TunnelScript"
        return $false
    }
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$TunnelScript) `
        -WindowStyle Hidden
    Start-Sleep -Seconds 8
    return $true
}

function Test-VpsHealth {
    try {
        $r = Invoke-WebRequest -Uri $VpsHealthUrl -UseBasicParsing -TimeoutSec 10
        $j = $r.Content | ConvertFrom-Json
        return [bool]$j.online
    } catch { return $false }
}

# ── Главный цикл проверки ────────────────────────────────────────────────
Write-Log "===== watchdog tick ====="

$ollamaOk = Test-OllamaLocal
if (-not $ollamaOk) {
    Write-Log "ollama local check: DOWN"
    $ollamaOk = Start-OllamaIfNeeded
} else {
    Write-Log "ollama local check: OK"
}

# Silero TTS — необязателен, если venv не установлен, тихо пропускаем
$sileroOk = Test-SileroLocal
if (-not $sileroOk) {
    Write-Log "silero local check: DOWN"
    $sileroOk = Start-SileroIfNeeded
} else {
    Write-Log "silero local check: OK"
}

$tunnelOk = Start-TunnelIfNeeded
Write-Log "tunnel started/verified: $tunnelOk"

# Даём VPS пару секунд после поднятия туннеля
Start-Sleep -Seconds 3
$vpsOk = Test-VpsHealth
Write-Log "vps /ai/health: $vpsOk"

$allOk = $ollamaOk -and $tunnelOk -and $vpsOk
$prevState = if (Test-Path $StateFile) { (Get-Content $StateFile -Raw).Trim() } else { 'UNKNOWN' }

if ($allOk) {
    if ($prevState -ne 'OK') {
        Show-Toast 'STH AI' 'ИИ восстановлен и снова доступен.' $false
        Write-Log "state changed: $prevState → OK (toast sent)"
    }
    'OK' | Out-File -FilePath $StateFile -Encoding utf8
} else {
    $reasons = @()
    if (-not $ollamaOk) { $reasons += 'Ollama не отвечает' }
    if (-not $tunnelOk) { $reasons += 'SSH-туннель не поднят' }
    if (-not $vpsOk)    { $reasons += 'VPS не видит модель' }
    $msg = $reasons -join '; '
    if ($prevState -ne 'FAIL') {
        Show-Toast 'STH AI — внимание' "ИИ упал: $msg" $true
        Write-Log "state changed: $prevState → FAIL (toast sent) — $msg"
    } else {
        Write-Log "still FAIL — $msg (toast suppressed)"
    }
    'FAIL' | Out-File -FilePath $StateFile -Encoding utf8
}
