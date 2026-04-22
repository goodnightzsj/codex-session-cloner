@echo off
REM cc-clean compatibility launcher — forwards to "aik claude …".
REM Same UTF-8 / venv / source-mode logic as aik.cmd; just prepends the
REM ``claude`` token so existing shell aliases keep working.
"%~dp0aik.cmd" claude %*
exit /b %ERRORLEVEL%
