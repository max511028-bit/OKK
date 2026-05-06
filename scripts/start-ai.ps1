
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

# -- Find ngrok.exe --
$ngrok = $null
$candidates = @(
    "$PSScriptRoot\..\ngrok.exe",
    "$PSScriptRoot\ngrok.exe",
    "$env:USERPROFILE\Downloads\ngrok.exe",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ngrok.ngrok_Microsoft.Winget.Source*\ngrok.exe",
    "C:\ngrok\ngrok.exe"
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
    Write-Host "  [ERR] ngrok.exe not found" -ForegroundColor Red
    Write-Host "  Place ngrok.exe next to start-ai.bat" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
Write-Host "  [OK]  ngrok: $ngrok" -ForegroundColor Green

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

# -- Start ngrok tunnel --
Write-Host "  ...   Starting ngrok tunnel..." -ForegroundColor Yellow

# Kill any existing ngrok
Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

$ngrokProc = Start-Process -FilePath $ngrok `
    -ArgumentList "http 11434" `
    -PassThru -WindowStyle Hidden

# Wait for ngrok local API to be ready, then get URL
Write-Host "  ...   Waiting for tunnel URL" -NoNewline -ForegroundColor Yellow
$tunnelUrl = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline -ForegroundColor Yellow
    try {
        $info = Invoke-RestMethod "http://localhost:4040/api/tunnels" -ErrorAction Stop
        $https = $info.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
        if ($https) { $tunnelUrl = $https.public_url; break }
    } catch {}
}
Write-Host ""

if (-not $tunnelUrl) {
    Write-Host "  [ERR] Could not get tunnel URL" -ForegroundColor Red
    Write-Host "  Make sure you ran: ngrok config add-authtoken YOUR_TOKEN" -ForegroundColor Yellow
    if ($ngrokProc -and -not $ngrokProc.HasExited) { $ngrokProc.Kill() }
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
    $ngrokProc | Wait-Process -ErrorAction SilentlyContinue
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
