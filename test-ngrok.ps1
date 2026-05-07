
Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

Write-Host "Testing ngrok..." -ForegroundColor Cyan

$ngrok = "C:\ngrok\ngrok.exe"

Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

$proc = Start-Process -FilePath $ngrok -ArgumentList "http 11434" -PassThru -WindowStyle Hidden

Write-Host "Waiting 25 seconds..." -ForegroundColor Yellow
Start-Sleep -Seconds 25

try {
    $info = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 3 -ErrorAction Stop
    $url = ($info.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1).public_url
    if ($url) {
        Write-Host "[OK] ngrok works! URL: $url" -ForegroundColor Green
    } else {
        Write-Host "[!!] ngrok started but no tunnel yet" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERR] ngrok API not responding" -ForegroundColor Red
}

if ($proc -and -not $proc.HasExited) { $proc.Kill() }

Write-Host ""
Write-Host "Press Enter to exit"
Read-Host
