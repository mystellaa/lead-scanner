@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto noenv

".venv\Scripts\python.exe" -c "import tkinter, PIL, onnxruntime" 2>nul
if errorlevel 1 goto nodep

start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:noenv
echo.
echo   [ERROR] Runtime .venv not found.
echo.
echo   Open a command prompt in THIS folder and run:
echo       py -3.12 -m venv .venv
echo       .venv\Scripts\python.exe -m pip install -r requirements-rapid.txt
echo.
pause
exit /b 1

:nodep
echo.
echo   [ERROR] Dependencies missing.
echo.
echo   Open a command prompt in THIS folder and run:
echo       .venv\Scripts\python.exe -m pip install -r requirements-rapid.txt
echo.
pause
exit /b 1
