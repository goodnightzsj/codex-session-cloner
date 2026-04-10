@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "install.ps1" %*
exit /b %ERRORLEVEL%
