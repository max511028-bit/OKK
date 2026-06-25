# STH voicecall — установщик зависимостей на Windows-ПК
# Запуск:
#   powershell -ExecutionPolicy Bypass -File voicecall\install_windows.ps1

$ErrorActionPreference = 'Stop'

Write-Host "=== STH voicecall: установка зависимостей ===" -ForegroundColor Cyan

# 1. Python должен быть установлен. Если нет — подсказываем.
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python не найден в PATH." -ForegroundColor Red
    Write-Host "Скачай и поставь с https://www.python.org/downloads/ (галочка 'Add to PATH')."
    exit 1
}
$pyVersion = & python --version 2>&1
Write-Host "Python: $pyVersion" -ForegroundColor Green

# 2. Создаём папку для секретов если её нет
$dataDir = 'C:\ProgramData\sth'
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    Write-Host "Создал $dataDir" -ForegroundColor Green
}

# 3. Создаём env-файл из шаблона если его ещё нет
$envFile = Join-Path $dataDir 'voicecall.env'
$envExample = Join-Path $PSScriptRoot 'env.example'
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "Создал шаблон $envFile — ОТРЕДАКТИРУЙ его и впиши свои SIP-данные." -ForegroundColor Yellow
    }
} else {
    Write-Host "Файл $envFile уже существует — не трогаю." -ForegroundColor Green
}

# 4. Ставим Python-зависимости
$reqFile = Join-Path $PSScriptRoot 'requirements.txt'
Write-Host ""
Write-Host "Устанавливаю pip-зависимости..." -ForegroundColor Cyan
& python -m pip install --upgrade pip --quiet
& python -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install не прошёл — смотри ошибки выше." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Готово ===" -ForegroundColor Green
Write-Host ""
Write-Host "Следующие шаги:"
Write-Host "  1. Открой $envFile в Блокноте и впиши свои SIP-данные из Novofon"
Write-Host "  2. Запусти тест регистрации:  python voicecall\test_register.py"
Write-Host "  3. Если регистрация ОК и баланс пополнен — запусти тест звонка:"
Write-Host "     python voicecall\test_call.py +79991234567"
