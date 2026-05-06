
# STH AI Launcher
# Runs Ollama + Cloudflare tunnel and publishes the address on the portal.

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

$VPS = "http://195.208.119.67/tasks/api/ai/url"

Write-Host ""
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host "         STH AI Launcher               " -ForegroundColor Cyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""

# -- Find cloudflared.exe --
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
    Write-Host "  [ERR] cloudflared.exe not found" -ForegroundColor Red
    Write-Host "  Place cloudflared-windows-amd64.exe next to start-ai.bat" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
Write-Host "  [OK]  cloudflared: $cf" -ForegroundColor Green

# -- Start Ollama (always restart to ensure OLLAMA_ORIGINS=*) --
Write-Host "  ...   Restarting Ollama with CORS enabled..." -ForegroundColor Yellow
Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$env:OLLAMA_ORIGINS = "*"
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden

Write-Host "  ...   Waiting for Ollama" -NoNewline -ForegroundColor Yellow
$ollamaRunning = $false
for ($i = 0; $i -lt 20; $i++) {
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
    Write-Host "  [OK]  Ollama started with CORS enabled" -ForegroundColor Green
} else {
    Write-Host "  [!!]  Ollama did not respond - continuing anyway" -ForegroundColor Yellow
}

$env:OLLAMA_ORIGINS = "*"

# -- Start tunnel --
Write-Host "  ...   Starting Cloudflare tunnel..." -ForegroundColor Yellow
$logFile = "$env:TEMP\sth_cf_$PID.log"

$cfProc = Start-Process -FilePath $cf `
    -ArgumentList "tunnel --url http://localhost:11434" `
    -RedirectStandardError $logFile `
    -PassThru -WindowStyle Hidden

Write-Host "  ...   Waiting for tunnel URL" -NoNewline -ForegroundColor Yellow
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
    Write-Host "  [ERR] Could not get tunnel URL" -ForegroundColor Red
    if ($cfProc -and -not $cfProc.HasExited) { $cfProc.Kill() }
    Start-Sleep -Seconds 5
    exit 1
}

Write-Host "  [OK]  Tunnel: $tunnelUrl" -ForegroundColor Green

# -- Send URL to VPS --
Write-Host "  ...   Publishing address on portal..." -ForegroundColor Yellow
try {
    $body = '{"url":"' + $tunnelUrl + '"}'
    Invoke-RestMethod -Uri $VPS -Method POST -Body $body -ContentType "application/json" -ErrorAction Stop | Out-Null
    Write-Host "  [OK]  Address published" -ForegroundColor Green
} catch {
    Write-Host "  [!!]  Could not publish: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host "  AI is available for all users!" -ForegroundColor Green
Write-Host "  Tunnel: $tunnelUrl" -ForegroundColor White
Write-Host "  Portal: http://195.208.119.67" -ForegroundColor White
Write-Host "  ========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Close this window to turn off AI for everyone" -ForegroundColor Yellow
Write-Host ""

# -- Keep alive while tunnel runs --
try {
    $cfProc | Wait-Process -ErrorAction SilentlyContinue
} catch {}

# -- Clear URL on server --
Write-Host ""
Write-Host "  Turning off AI for all users..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri $VPS -Method POST -Body '{"url":null}' -ContentType "application/json" -ErrorAction SilentlyContinue | Out-Null
} catch {}

if (Test-Path $logFile) { Remove-Item $logFile -Force -ErrorAction SilentlyContinue }

Write-Host "  Done. AI is offline." -ForegroundColor Green
Start-Sleep -Seconds 2
