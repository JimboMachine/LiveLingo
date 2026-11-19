@echo off
chcp 65001 >nul
title 即時翻譯（關掉這個視窗就會結束程式）
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto notinstalled
if not exist "models\translategemma-4b.gguf" goto notinstalled

echo 即時翻譯啟動中... 這個黑色視窗會自己縮到工作列，請不要關掉它（關掉就等於結束程式）。
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto crashed
exit /b 0

:notinstalled
echo 還沒安裝完成，請先點兩下 setup.bat。
pause
exit /b 1

:crashed
echo.
echo 程式發生錯誤。請把這個視窗的內容截圖傳給 Claude。
pause
exit /b 1
