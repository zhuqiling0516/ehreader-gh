@echo off
chcp 65001 >nul
rem 在电脑上预览手机界面（与安卓上同一套代码）
cd /d "%~dp0"
py -3.13 main.py
if errorlevel 1 (
    echo.
    echo 启动失败：请先执行  py -3.13 -m pip install kivy requests pillow
    pause
)
