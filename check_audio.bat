@echo off
chcp 65001 >nul
title 即時翻譯 收音檢查
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto notinstalled
".venv\Scripts\python.exe" tools\audio_check.py
echo.
echo 檢查結束。語音翻譯有問題的話，請把這個視窗截圖傳給 Claude。
pause
exit /b 0

:notinstalled
echo 還沒安裝完成，請先點兩下 setup.bat。
pause
exit /b 1
