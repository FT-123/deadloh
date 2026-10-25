@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem UTF-8, чтобы русский текст и emoji в выводе Python не падали на cp866.
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
    echo [ERROR] Сборка завершилась с кодом %RC%.
) else (
    echo [OK] Готово. Zip лежит в папке dist.
)
pause
exit /b %RC%
