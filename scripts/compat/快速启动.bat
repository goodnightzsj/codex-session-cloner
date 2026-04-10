@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%..\..\codex-session-cloner.cmd" %*
exit /b %ERRORLEVEL%
