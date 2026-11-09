@echo off
chcp 65001 >nul
cd /d %~dp0
echo 启动中文知识库问答 CLI（quit 退出）...
.venv\Scripts\python.exe run_chat.py
pause
