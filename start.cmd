@echo off
setlocal
cd /d "%~dp0"
uv run --project backend python start.py
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
