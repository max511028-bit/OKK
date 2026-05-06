
# STH AI Launcher
# Starts ngrok tunnel and publishes address on the portal.
# Restarts Ollama with OLLAMA_ORIGINS=* so CORS preflight works for all browsers.

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
    Write-Host "  [ERR] ngrok.exe not found" -ForegroundColor Red
    Write-Host "  Place ngrok.exe next to start-ai.bat" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    exit 1
}
Write-Host "  [OK]  ngrok: $ngrok" -ForegroundColor Green

# -- Restart Ollama with OLLAMA_ORIGINS=* (required for browser CORS) --
Write-Host "  ...   Restarting Ollama with CORS enabled..." -ForegroundColor Yellow
Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$env:OLLAMA_ORIGINS = "*"
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
Write-Host "  ...   Waiting for Ollama to start..." -NoNewline -ForegroundColor Yellow
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline -ForegroundColor Yellow
    try {
        Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        break
    } catch {}
}
Write-Host ""
Write-Host "  [OK]  Ollama ready (CORS enabled)" -ForegroundColor Green

# -- Check if ngrok is already running with a tunnel --
Write-Host "  ...   Checking ngrok..." -ForegroundColor Yellow
$tunnelUrl = $null
try {
    $info = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 3 -ErrorAction Stop
    $https = $info.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
    if ($https) {
        $tunnelUrl = $https.public_url
        Write-Host "  [OK]  ngrok already running: $tunnelUrl" -ForegroundColor Green
    }
} catch {}

# -- Start ngrok only if not already running --
if (-not $tunnelUrl) {
    Write-Host "  ...   Starting ngrok tunnel..." -ForegroundColor Yellow
    $ngrokConfig = "$env:LOCALAPPDATA\ngrok\ngrok.yml"
    Start-Process -FilePath $ngrok -ArgumentList "http 11434 --config `"$ngrokConfig`""
}

# -- Wait for tunnel URL if ngrok was just started --
if (-not $tunnelUrl) {
    Write-Host "  ...   Waiting for tunnel URL" -NoNewline -ForegroundColor Yellow
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        Write-Host "." -NoNewline -ForegroundColor Yellow
        try {
            $info = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 2 -ErrorAction Stop
            $https = $info.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
            if ($https) { $tunnelUrl = $https.public_url; break }
        } catch {}
    }
    Write-Host ""
    if (-not $tunnelUrl) {
        Write-Host "  [ERR] Could not get tunnel URL" -ForegroundColor Red
        Write-Host "  Run ngrok manually: ngrok http 11434" -ForegroundColor Yellow
        Start-Sleep -Seconds 5
        exit 1
    }
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

# -- Keep alive: loop until Ctrl+C or tunnel disappears --
Write-Host "  Press Ctrl+C to turn off AI for everyone" -ForegroundColor Yellow
Write-Host ""
while ($true) {
    Start-Sleep -Seconds 15
    try {
        $check = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 3 -ErrorAction Stop
        $alive = $check.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
        if (-not $alive) { break }
    } catch { break }
}

# -- Clear URL on server --
Write-Host ""
Write-Host "  Turning off AI for all users..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri $VPS -Method POST -Body '{"url":null}' -ContentType "application/json" -ErrorAction SilentlyContinue | Out-Null
} catch {}

Write-Host "  Done. AI is offline." -ForegroundColor Green
Start-Sleep -Seconds 2
