
# STH AI Launcher (v2 — bulletproof)
# Starts ngrok tunnel and publishes address on the portal.
# Restarts Ollama with OLLAMA_ORIGINS=* so CORS preflight works for all browsers.
#
# Improvements over v1:
#   * Captures ngrok stdout/stderr to a log file (no more silent failures)
#   * Detects ngrok process exit immediately (fail-fast instead of 60s timeout)
#   * Pre-flight: kills stale ngrok, validates config/auth
#   * Detects "ERR_NGROK_108" (session limit) and shows fix instructions
#   * Better, specific error messages
#   * Self-test mode: `start-ai.ps1 -Diagnose` runs all checks without launching

param(
    [switch]$Diagnose
)

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

$VPS         = "http://195.208.119.67/tasks/api/ai/url"
$NGROK_API   = "http://127.0.0.1:4040/api/tunnels"
$LOG_DIR     = "$env:LOCALAPPDATA\sth-ai-launcher"
$NGROK_LOG   = "$LOG_DIR\ngrok.log"
$LAUNCH_LOG  = "$LOG_DIR\launch.log"

if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Log {
    param([string]$msg, [string]$color = 'White')
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host "  $msg" -ForegroundColor $color
    Add-Content -Path $LAUNCH_LOG -Value $line -ErrorAction SilentlyContinue
}
function LogErr  { param($m) Log "[ERR] $m"  'Red' }
function LogOK   { param($m) Log "[OK]  $m"  'Green' }
function LogWarn { param($m) Log "[!!]  $m"  'Yellow' }
function LogInfo { param($m) Log "...   $m"  'Yellow' }

Write-Host ""
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host "         STH AI Launcher v2            " -ForegroundColor Cyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""
"=== Launch at $(Get-Date) ===" | Add-Content -Path $LAUNCH_LOG

# =============================================================
# STEP 1. Find ngrok.exe
# =============================================================
$ngrok = $null
$candidates = @(
    "C:\ngrok\ngrok.exe",
    "$PSScriptRoot\..\ngrok.exe",
    "$PSScriptRoot\ngrok.exe",
    "$env:USERPROFILE\Downloads\ngrok.exe"
)
foreach ($c in $candidates) {
    $found = Get-Item $c -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $ngrok = $found.FullName; break }
}
if (-not $ngrok) {
    $inPath = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($inPath) { $ngrok = $inPath.Source }
}
if (-not $ngrok) {
    LogErr "ngrok.exe not found"
    Write-Host "  Place ngrok.exe in C:\ngrok\ or next to start-ai.bat" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
LogOK "ngrok: $ngrok"

# =============================================================
# STEP 2. Validate ngrok config (authtoken, syntax)
# =============================================================
$ngrokConfig = "$env:LOCALAPPDATA\ngrok\ngrok.yml"
if (-not (Test-Path $ngrokConfig)) {
    LogErr "ngrok config not found: $ngrokConfig"
    Write-Host "  Run: ngrok config add-authtoken <YOUR_TOKEN>" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
LogOK "Config: $ngrokConfig"

# `ngrok config check` validates yml + authtoken presence
$check = & $ngrok config check --config $ngrokConfig 2>&1
if ($LASTEXITCODE -ne 0) {
    LogErr "ngrok config invalid:"
    Write-Host $check -ForegroundColor Red
    Start-Sleep -Seconds 10
    exit 1
}
LogOK "Config valid"

# =============================================================
# STEP 3. Kill stale ngrok processes (free tier = 1 session)
# =============================================================
$stale = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
if ($stale) {
    LogWarn "Found $($stale.Count) stale ngrok process(es), killing..."
    $stale | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    LogOK "Stale ngrok killed"
}

# =============================================================
# STEP 4. Ensure Ollama is running + CORS configured
# Uses User-env OLLAMA_ORIGINS=* (set once, persists across reboots).
# Does NOT kill+restart Ollama (caused pipe-blocking bugs in v1).
# =============================================================
function Test-OllamaUp {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Test-OllamaCors {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -Method Options `
            -Headers @{ "Origin" = "http://195.208.119.67"; "Access-Control-Request-Method" = "POST" } `
            -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        $acao = [string]$r.Headers["Access-Control-Allow-Origin"]
        return ($acao -eq "*" -or $acao.Length -gt 0)
    } catch { return $false }
}

# Ensure OLLAMA_ORIGINS=* is set persistently (User scope)
$userOrigins = [Environment]::GetEnvironmentVariable("OLLAMA_ORIGINS", "User")
if ($userOrigins -ne "*") {
    LogInfo "Setting OLLAMA_ORIGINS=* in User env (one-time, persists across reboots)"
    [Environment]::SetEnvironmentVariable("OLLAMA_ORIGINS", "*", "User")
    LogWarn "Ollama needs a restart to pick up new env var. Right-click Ollama tray icon -> Quit, then relaunch from Start menu, then re-run start-ai.bat"
    Start-Sleep -Seconds 15
    exit 1
}
LogOK "OLLAMA_ORIGINS=* (User env)"

# If Ollama is not running, try to launch the tray app (it reads env vars on launch)
if (-not (Test-OllamaUp)) {
    LogWarn "Ollama not running on 11434. Trying to launch tray app..."
    $trayPaths = @(
        "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe",
        "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
    )
    $trayExe = $null
    foreach ($p in $trayPaths) { if (Test-Path $p) { $trayExe = $p; break } }
    if ($trayExe) {
        Start-Process -FilePath $trayExe -WindowStyle Hidden
        Write-Host "  ...   Waiting for Ollama to start" -NoNewline -ForegroundColor Yellow
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 2
            Write-Host "." -NoNewline -ForegroundColor Yellow
            if (Test-OllamaUp) { break }
        }
        Write-Host ""
    } else {
        LogErr "Ollama tray app not found. Launch it manually from Start menu."
        Start-Sleep -Seconds 10; exit 1
    }
}

if (-not (Test-OllamaUp)) {
    LogErr "Ollama still not responding on 11434 after launch attempt"
    Write-Host "  Open Start menu -> Ollama, wait for tray icon, then re-run." -ForegroundColor Yellow
    Start-Sleep -Seconds 15; exit 1
}
LogOK "Ollama responding on 11434"

if (-not (Test-OllamaCors)) {
    LogErr "CORS not configured on Ollama (preflight failed)"
    Write-Host "  Quit Ollama tray (right-click icon -> Quit) and relaunch from Start menu." -ForegroundColor Yellow
    Write-Host "  Env var is set correctly, but Ollama only reads it on launch." -ForegroundColor Yellow
    Start-Sleep -Seconds 15; exit 1
}
LogOK "Ollama CORS verified"

if ($Diagnose) {
    LogOK "All pre-flight checks passed (diagnose mode - not starting tunnel)"
    Start-Sleep -Seconds 5
    exit 0
}

# =============================================================
# STEP 5. Start ngrok with logging + exit detection
# =============================================================
LogInfo "Starting ngrok tunnel (log: $NGROK_LOG)..."
if (Test-Path $NGROK_LOG) { Remove-Item $NGROK_LOG -Force -ErrorAction SilentlyContinue }

# Use inline `http 11434` instead of named tunnel from yml — bulletproof
# against config-section drift. Config is still used for authtoken only.
$ngrokProc = Start-Process -FilePath $ngrok `
    -ArgumentList @("http", "11434", "--config", $ngrokConfig, "--host-header=rewrite", "--log=stdout", "--log-format=logfmt") `
    -RedirectStandardOutput $NGROK_LOG `
    -RedirectStandardError "$NGROK_LOG.err" `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

# Check if ngrok already died
if ($ngrokProc.HasExited) {
    LogErr "ngrok exited immediately (code $($ngrokProc.ExitCode))"
    Write-Host ""
    Write-Host "  --- ngrok log (last 30 lines) ---" -ForegroundColor Yellow
    if (Test-Path $NGROK_LOG)      { Get-Content $NGROK_LOG     -Tail 30 | Write-Host -ForegroundColor Gray }
    if (Test-Path "$NGROK_LOG.err"){ Get-Content "$NGROK_LOG.err" -Tail 30 | Write-Host -ForegroundColor Gray }
    Write-Host "  ---------------------------------" -ForegroundColor Yellow

    # Check for common errors
    $logText = ""
    if (Test-Path $NGROK_LOG)       { $logText += (Get-Content $NGROK_LOG     -Raw) }
    if (Test-Path "$NGROK_LOG.err") { $logText += (Get-Content "$NGROK_LOG.err" -Raw) }

    if ($logText -match "ERR_NGROK_108" -or $logText -match "session.*limit") {
        Write-Host ""
        Write-Host "  ### SESSION LIMIT (free tier = 1 active tunnel) ###" -ForegroundColor Red
        Write-Host "  Kill old session at: https://dashboard.ngrok.com/agents" -ForegroundColor Yellow
    } elseif ($logText -match "ERR_NGROK_105" -or $logText -match "auth") {
        Write-Host ""
        Write-Host "  ### AUTH PROBLEM ###" -ForegroundColor Red
        Write-Host "  Re-run: ngrok config add-authtoken <YOUR_TOKEN>" -ForegroundColor Yellow
    } elseif ($logText -match "bind.*4040" -or $logText -match "address already in use") {
        Write-Host ""
        Write-Host "  ### Port 4040 busy ###" -ForegroundColor Red
        Write-Host "  Find it: netstat -ano | findstr :4040" -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 15
    exit 1
}

# =============================================================
# STEP 6. Wait for tunnel URL (with fail-fast on process exit)
# =============================================================
Write-Host "  ...   Waiting for tunnel URL" -NoNewline -ForegroundColor Yellow
$tunnelUrl = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline -ForegroundColor Yellow

    # Fail-fast: ngrok died?
    if ($ngrokProc.HasExited) {
        Write-Host ""
        LogErr "ngrok exited during startup (code $($ngrokProc.ExitCode))"
        if (Test-Path $NGROK_LOG) { Get-Content $NGROK_LOG -Tail 20 | Write-Host -ForegroundColor Gray }
        Start-Sleep -Seconds 10
        exit 1
    }

    try {
        $info = Invoke-RestMethod $NGROK_API -TimeoutSec 2 -ErrorAction Stop
        $https = $info.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
        if ($https) { $tunnelUrl = $https.public_url; break }
    } catch {}
}
Write-Host ""
if (-not $tunnelUrl) {
    LogErr "ngrok is running but no HTTPS tunnel appeared in 30s"
    Write-Host "  Inspect at: http://127.0.0.1:4040" -ForegroundColor Yellow
    if (Test-Path $NGROK_LOG) {
        Write-Host "  --- ngrok log tail ---" -ForegroundColor Yellow
        Get-Content $NGROK_LOG -Tail 20 | Write-Host -ForegroundColor Gray
    }
    Start-Sleep -Seconds 15
    exit 1
}
LogOK "Tunnel: $tunnelUrl"

# =============================================================
# STEP 7. Publish URL to VPS
# =============================================================
LogInfo "Publishing address on portal..."
try {
    $body = '{"url":"' + $tunnelUrl + '"}'
    Invoke-RestMethod -Uri $VPS -Method POST -Body $body -ContentType "application/json" -TimeoutSec 10 -ErrorAction Stop | Out-Null
    LogOK "Address published"
} catch {
    LogWarn "Could not publish: $_"
    Write-Host "  Is VPS reachable? curl $VPS" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host "  AI is available for all users!" -ForegroundColor Green
Write-Host "  Tunnel: $tunnelUrl" -ForegroundColor White
Write-Host "  Portal: http://195.208.119.67" -ForegroundColor White
Write-Host "  Logs:   $LOG_DIR" -ForegroundColor DarkGray
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Close this window to turn off AI for everyone" -ForegroundColor Yellow
Write-Host "  Press Ctrl+C to turn off AI for everyone" -ForegroundColor Yellow
Write-Host ""

# =============================================================
# STEP 8. Keep alive — break on tunnel death or process exit
# =============================================================
while ($true) {
    Start-Sleep -Seconds 15
    if ($ngrokProc.HasExited) {
        LogWarn "ngrok process exited (code $($ngrokProc.ExitCode))"
        break
    }
    try {
        $check = Invoke-RestMethod $NGROK_API -TimeoutSec 3 -ErrorAction Stop
        $alive = $check.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
        if (-not $alive) { LogWarn "Tunnel disappeared"; break }
    } catch { LogWarn "ngrok API unreachable"; break }
}

# =============================================================
# STEP 9. Cleanup — clear URL on VPS, kill ngrok
# =============================================================
Write-Host ""
LogInfo "Turning off AI for all users..."
try {
    Invoke-RestMethod -Uri $VPS -Method POST -Body '{"url":null}' -ContentType "application/json" -TimeoutSec 5 -ErrorAction SilentlyContinue | Out-Null
} catch {}

if (-not $ngrokProc.HasExited) {
    Stop-Process -Id $ngrokProc.Id -Force -ErrorAction SilentlyContinue
}

LogOK "Done. AI is offline."
Start-Sleep -Seconds 3
