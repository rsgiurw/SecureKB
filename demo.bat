@echo off
chcp 65001 >nul
cd /d %~dp0
echo 启动 Web Demo，浏览器打开 http://127.0.0.1:8000 （Ctrl+C 停止）
.venv\Scripts\python.exe web_demo.py
pause
