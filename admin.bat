@echo off
chcp 65001 >nul
cd /d %~dp0
:menu
echo.
echo ===== 知识库管理（管理员专用）=====
echo   1) 查看当前状态（模式 / 文档数）
echo   2) 切换到 严谨模式（strict，默认）
echo   3) 切换到 通俗模式（friendly）
echo   4) 退出
set /p c=请输入编号: 
if "%c%"=="1" .venv\Scripts\python.exe scripts\kb_admin.py status
if "%c%"=="2" .venv\Scripts\python.exe scripts\kb_admin.py mode strict
if "%c%"=="3" .venv\Scripts\python.exe scripts\kb_admin.py mode friendly
if "%c%"=="4" exit /b
goto menu
