' Запускает ai-watchdog.ps1 полностью невидимо (без вспышки окна PowerShell)
Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\user\Documents\КЛод\ОКК\OKK\scripts\ai-watchdog.ps1""", 0, False
