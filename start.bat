@echo off
chcp 65001 >nul
cd /d %~dp0
echo ================================================
echo   SecureKB 一键启动
echo ================================================
echo.
echo [1/2] 启动后端（8000 端口）...
start "SecureKB-后端" cmd /k ".venv\Scripts\python.exe main.py"
timeout /t 8 /nobreak >nul
echo [2/2] 启动前端（5000 端口）...
cd frontend
start "SecureKB-前端" cmd /k "npm run dev"
cd ..
timeout /t 6 /nobreak >nul
echo.
echo ✅ 启动完成！浏览器打开：http://localhost:5000
echo    登录账户：admin / Admin@2026（管理员，可见全部文档）
echo              tech / Tech@2026（技术部员工，文档受限）
echo    管理面板密钥：.env 文件里 FASTAPI_AUTH_KEY= 后面的完整值
echo.
echo 关闭对应的两个命令行窗口即可停止服务。
start "" http://localhost:5000
