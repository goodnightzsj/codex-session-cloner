@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "LAUNCH_MODE=%CST_LAUNCH_MODE%"
if "%LAUNCH_MODE%"=="" set "LAUNCH_MODE=%CSC_LAUNCH_MODE%"
if "%LAUNCH_MODE%"=="" set "LAUNCH_MODE=auto"

if /I "%LAUNCH_MODE%"=="installed" (
  "%SCRIPT_DIR%.venv\Scripts\codex-session-toolkit.exe" %*
  exit /b %ERRORLEVEL%
)

if /I "%LAUNCH_MODE%"=="auto" (
  if not exist "%SCRIPT_DIR%.git" (
    if exist "%SCRIPT_DIR%.venv\Scripts\codex-session-toolkit.exe" (
      "%SCRIPT_DIR%.venv\Scripts\codex-session-toolkit.exe" %*
      exit /b %ERRORLEVEL%
    )
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%codex-session-toolkit.ps1" %*
exit /b %ERRORLEVEL%
