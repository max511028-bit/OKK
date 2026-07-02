# STH · обёртка для запуска voicecall/dispatch_agent.py как постоянного
# фонового процесса при входе пользователя в систему.
#
# dispatch_agent.py сам по себе — бесконечный цикл (тихо опрашивает
# портал, звонит когда попросят), эта обёртка просто:
#   1) держит его в рабочей директории voicecall/ (там лежат сценарии,
#      tts_cache, _sip_config.py и т.д. — относительные пути важны);
#   2) пишет весь вывод в постоянный лог-файл (в самом процессе
#      консоли нет — Scheduled Task запускает его скрыто);
#   3) если python неожиданно упал — перезапускает через 10 сек,
#      а не оставляет обзвон вообще без агента до следующего логина.

$ErrorActionPreference = 'Continue'
$voicecallDir = 'C:\Users\user\Documents\КЛод\ОКК\OKK\voicecall'
$logPath = 'C:\ProgramData\sth\dispatch-agent.log'

Set-Location $voicecallDir

while ($true) {
    "$(Get-Date -Format o) — (пере)запускаю dispatch_agent.py" | Out-File -FilePath $logPath -Append -Encoding utf8
    # PowerShell-редирект (*>>) прогоняет вывод дочернего процесса через
    # свою собственную типизацию потоков и перекодирует его — с
    # кириллицей (dispatch_agent.py сам пишет строго UTF-8 через
    # sys.stdout.reconfigure) это давало битую кашу в логе. cmd.exe
    # делает редирект на уровне ОС побайтово, без реинтерпретации.
    cmd.exe /c "python dispatch_agent.py >> `"$logPath`" 2>&1"
    "$(Get-Date -Format o) — dispatch_agent.py завершился (код выхода $LASTEXITCODE), перезапуск через 10 сек" | Out-File -FilePath $logPath -Append -Encoding utf8
    Start-Sleep -Seconds 10
}
