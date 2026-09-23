@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Create .venv and install the project first. See README.md. 1>&2
    exit /b 2
)
"%~dp0.venv\Scripts\python.exe" -m wxtext %*
exit /b %errorlevel%
