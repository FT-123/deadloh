@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem UTF-8 output for Python (Russian text/emoji).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" build_release.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERROR] Build failed with exit code %RC%.
) else (
    echo [OK] Done. The zip is in the dist folder.
)
pause
exit /b %RC%
