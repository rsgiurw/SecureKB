@echo off
cd /d %~dp0
echo Starting KB Chat CLI (type quit to exit)...
.venv\Scripts\python.exe run_chat.py
pause
