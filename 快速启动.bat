@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "csc-launcher.ps1" %*
