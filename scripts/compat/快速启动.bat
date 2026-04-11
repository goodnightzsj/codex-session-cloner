@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%..\..\codex-session-toolkit.cmd" %*
exit /b %ERRORLEVEL%
