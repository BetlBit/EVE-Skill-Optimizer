@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python 3.11 virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :fail
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -e ".[dev]"
if errorlevel 1 goto :fail

powershell -NoProfile -ExecutionPolicy Bypass -File ".\build_release.ps1"
if errorlevel 1 goto :fail

echo.
echo Build complete. See the release folder.
pause
exit /b 0

:fail
echo.
echo BUILD FAILED.
pause
exit /b 1
