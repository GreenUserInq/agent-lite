@echo off
chcp 65001 >nul
title Агент
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\services.ps1"
"%~dp0ComfyUI_windows_portable\python_embeded\python.exe" -s "%~dp0app\app.py"
if errorlevel 1 pause
