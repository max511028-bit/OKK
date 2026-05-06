@echo off
chcp 65001 >nul 2>&1
PowerShell -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\start-ai.ps1"
pause
