@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%.venv\Scripts\codex-session-cloner.exe" (
  "%SCRIPT_DIR%.venv\Scripts\codex-session-cloner.exe" %*
  exit /b %ERRORLEVEL%
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%codex-session-cloner.ps1" %*
exit /b %ERRORLEVEL%
