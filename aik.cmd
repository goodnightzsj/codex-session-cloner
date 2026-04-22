@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "LAUNCH_MODE=%AIK_LAUNCH_MODE%"
if "%LAUNCH_MODE%"=="" set "LAUNCH_MODE=%CST_LAUNCH_MODE%"
if "%LAUNCH_MODE%"=="" set "LAUNCH_MODE=%CSC_LAUNCH_MODE%"
if "%LAUNCH_MODE%"=="" set "LAUNCH_MODE=auto"

REM Force UTF-8 everywhere so Chinese paths/filenames/output are not mangled
REM by legacy Windows codepages (cp936/cp1252).
if "%PYTHONUTF8%"=="" set "PYTHONUTF8=1"
if "%PYTHONIOENCODING%"=="" set "PYTHONIOENCODING=utf-8"

if /I "%LAUNCH_MODE%"=="installed" (
  "%SCRIPT_DIR%.venv\Scripts\aik.exe" %*
  exit /b %ERRORLEVEL%
)

if /I "%LAUNCH_MODE%"=="auto" (
  if not exist "%SCRIPT_DIR%.git" (
    if exist "%SCRIPT_DIR%.venv\Scripts\aik.exe" (
      "%SCRIPT_DIR%.venv\Scripts\aik.exe" %*
      exit /b %ERRORLEVEL%
    )
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%aik.ps1" %*
exit /b %ERRORLEVEL%
