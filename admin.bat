@echo off
cd /d %~dp0
echo ===== KB Admin Console =====
echo   1) Show status (mode / doc count)
echo   2) Switch to STRICT mode (default)
echo   3) Switch to FRIENDLY mode
set /p c=Choose [1-3]: 
if not defined c exit /b
if "%c%"=="1" .venv\Scripts\python.exe scripts\kb_admin.py status
if "%c%"=="2" .venv\Scripts\python.exe scripts\kb_admin.py mode strict
if "%c%"=="3" .venv\Scripts\python.exe scripts\kb_admin.py mode friendly
if not "%c%"=="1" if not "%c%"=="2" if not "%c%"=="3" echo Invalid choice: %c%
echo.
echo Done. (Run this file again to do another action.)
pause
