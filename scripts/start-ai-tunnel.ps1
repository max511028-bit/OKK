# STH · SSH reverse tunnel: твой комп (Ollama 11434) → VPS:21434
# Запускается как Windows-сервис через nssm (install-ai-tunnel-service.ps1).
# Внутри — бесконечный цикл: умер ssh → перезапуск через 5 сек.

$ErrorActionPreference = 'Continue'

$logDir = 'C:\ProgramData\sth'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir 'ai-tunnel.log'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$ts  $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

$key = 'C:\ProgramData\sth\ai-tunnel.key'
$vps = 'root@195.208.119.67'
# OpenSSH в System32 (Sysnative из x86, но SYSTEM-таск 64-битный — System32 видит правильно)
$sshExe = 'C:\Windows\System32\OpenSSH\ssh.exe'
if (-not (Test-Path $sshExe)) { $sshExe = 'ssh.exe' }
$stderrLog = Join-Path $logDir 'ai-tunnel.ssh-stderr.log'

# Ключевые параметры:
#   -N              — не открывать shell, только туннель
#   -T              — не выделять терминал
#   -o ServerAliveInterval=30  — пинг каждые 30с, чтобы NAT/uBoost не дропали
#   -o ServerAliveCountMax=3   — 3 промаха = разрыв (≈90с)
#   -o ExitOnForwardFailure=yes — упасть сразу если форвард не подцепился
#   -o StrictHostKeyChecking=accept-new — первый раз принять fingerprint
#   -R 21434:127.0.0.1:11434 — на VPS порт 21434 → моя машина 11434

Write-Log "tunnel watchdog started"

# Сколько подряд получено exit 255 (ExitOnForwardFailure = порт занят зомби-сессией).
# После 3 промахов делаем precleanup на VPS: убиваем чужие sshd, держащие 21434.
$consecutiveForwardFails = 0

while ($true) {
    # Precleanup: убрать зомби-tunnel на VPS, если последние попытки падали с 255
    if ($consecutiveForwardFails -ge 3) {
        Write-Log "precleanup: forwarding failed $consecutiveForwardFails times in a row, killing stale tunnels on VPS"
        try {
            # один короткий ssh-вызов: найти sshd, держащие 127.0.0.1:21434, и убить их
            $cleanupCmd = "pids=`$(ss -lntp 2>/dev/null | awk '/127.0.0.1:21434/ {print `$NF}' | grep -oP 'pid=\K[0-9]+' | sort -u); [ -n `"`$pids`" ] && kill -9 `$pids 2>/dev/null; sleep 1; echo done"
            & $sshExe '-i' $key '-o' 'ConnectTimeout=10' '-o' 'StrictHostKeyChecking=accept-new' '-o' "UserKnownHostsFile=C:\ProgramData\sth\known_hosts" $vps $cleanupCmd 2>&1 | Out-Null
            $consecutiveForwardFails = 0
            Write-Log "precleanup: done"
        } catch {
            Write-Log "precleanup failed: $($_.Exception.Message)"
        }
    }

    Write-Log "starting ssh tunnel..."
    $args = @(
        '-vv',
        '-i', $key,
        '-N', '-T',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'UserKnownHostsFile=C:\ProgramData\sth\known_hosts',
        '-R', '21434:127.0.0.1:11434',
        $vps
    )
    try {
        # stderr ssh пишем в отдельный файл — оттуда видна причина отбоя -R
        $proc = Start-Process -FilePath $sshExe -ArgumentList $args -NoNewWindow -PassThru -Wait `
            -RedirectStandardError $stderrLog -RedirectStandardOutput (Join-Path $logDir 'ai-tunnel.ssh-stdout.log')
        Write-Log "ssh exited with code $($proc.ExitCode)"
        # 255 = generic failure; чаще всего это ExitOnForwardFailure (порт занят на VPS)
        if ($proc.ExitCode -eq 255) {
            $consecutiveForwardFails++
        } else {
            $consecutiveForwardFails = 0
        }
    } catch {
        Write-Log "ssh launch failed: $($_.Exception.Message)"
    }
    # Пауза между попытками. 2 сек = быстрое восстановление после VPN-switch
    # (~4с total cycle), без спама при длительном обрыве.
    Start-Sleep -Seconds 2

    # Ротация лога
    try {
        if ((Get-Item $logFile -ErrorAction SilentlyContinue).Length -gt 2MB) {
            $lines = Get-Content $logFile -Tail 1000
            $lines | Out-File -FilePath $logFile -Encoding utf8
        }
    } catch {}
}
