@echo off
chcp 65001 >nul
title 即時翻譯 卸載
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

rem 先確認這裡真的是即時翻譯的資料夾，才不會刪錯地方
if not exist "%DIR%\app.py" goto wrongplace
if not exist "%DIR%\setup.bat" goto wrongplace
if not exist "%DIR%\core\translate.py" goto wrongplace

echo.
echo 即將卸載「即時翻譯」，安裝位置：
echo   %DIR%
echo.
echo 會刪除：程式本身、下載的翻譯模型和語音模型、專用環境、你的設定和專有名詞表（共約 5 GB）。
echo 只會刪這個程式自己的檔案，資料夾裡其他不相關的檔案和電腦上其他東西都不會動。
echo.
set "ANS="
set /p "ANS=確定要卸載，請輸入 Y 再按 Enter（直接按 Enter 是取消）："
if /i not "%ANS%"=="Y" goto cancel

echo.
echo 正在關閉還在執行的翻譯程式...
set "LT_DIR=%DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($env:LT_DIR + '\', [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force" >nul 2>&1
timeout /t 2 /nobreak >nul

echo 正在刪除模型和專用環境（檔案很多，請稍等）...
for %%D in (.venv models engine) do if exist "%DIR%\%%D\" rmdir /s /q "%DIR%\%%D"
set "LEFT="
for %%D in (.venv models engine) do if exist "%DIR%\%%D\" set "LEFT=1"
if defined LEFT goto busy

echo 正在刪除程式...
for %%D in (core tools __pycache__) do if exist "%DIR%\%%D\" rmdir /s /q "%DIR%\%%D"
for %%F in (app.py README.md glossary.txt requirements.txt config.json app.log engine.log setup.bat start.bat check_audio.bat) do if exist "%DIR%\%%F" del /q "%DIR%\%%F"
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LiveTranslator" /f >nul 2>&1

echo.
echo 卸載完成。
echo 如果當初有讓安裝程式幫你裝 Python 3.12，而且你用不到它，可以到「設定 - 應用程式」另外移除。
echo.
pause
cd /d "%TEMP%"
rem 最後把這個卸載檔自己和空資料夾也刪掉（資料夾裡如果還有別的檔案就會留著）
(goto) 2>nul & del /q "%DIR%\uninstall.bat" & rmdir "%DIR%" 2>nul

:busy
echo.
echo 有些檔案還在使用中，刪不掉。
echo 請先把翻譯小視窗和工作列上的黑色視窗關掉，再點一次 uninstall.bat。
pause
exit /b 1

:cancel
echo 已取消，沒有刪除任何東西。
pause
exit /b 0

:wrongplace
echo 這個檔案要放在即時翻譯的資料夾裡才能用，沒有刪除任何東西。
pause
exit /b 1
