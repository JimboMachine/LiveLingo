@echo off
chcp 65001 >nul
title 即時翻譯 安裝
cd /d "%~dp0"
set "PYTHONUTF8=1"

:detect
set "PY="
for %%V in (3.12 3.13 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if defined PY goto havepy
if defined TRIED_WINGET goto nopython

echo.
echo 這台電腦沒有可用的 Python（需要 3.10 到 3.14 版）。
echo 按任意鍵會用 Windows 內建的 winget 自動安裝 Python 3.12（免費，官方版本）。
echo 不想安裝的話，直接關掉這個視窗就好。
pause >nul
set "TRIED_WINGET=1"
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
goto detect

:nopython
echo.
echo 自動安裝 Python 沒有成功。
echo 請到 https://www.python.org/downloads/windows/ 下載 Python 3.12 的 Windows installer (64-bit)，
echo 安裝時把「Add python.exe to PATH」打勾，裝好之後再點兩下 setup.bat。
pause
exit /b 1

:havepy
echo.
echo [1/3] 建立程式專用的環境...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto fail

echo.
echo [2/3] 安裝需要的元件（約 500 MB）...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail

echo.
echo [3/3] 下載翻譯引擎和模型（約 4.2 GB，依網路速度要幾分鐘到幾十分鐘，中斷了再執行一次會接著下載）...
".venv\Scripts\python.exe" tools\download.py
if errorlevel 1 goto fail

rem 登記到 Windows 的「已安裝的應用程式」清單，之後可以從那裡或點 uninstall.bat 卸載
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LiveTranslator"
reg add "%KEY%" /v DisplayName /t REG_SZ /d "即時翻譯 Live Translator" /f >nul 2>&1
reg add "%KEY%" /v DisplayVersion /t REG_SZ /d "1.2" /f >nul 2>&1
reg add "%KEY%" /v Publisher /t REG_SZ /d "Live Translator" /f >nul 2>&1
reg add "%KEY%" /v InstallLocation /t REG_SZ /d "%DIR%" /f >nul 2>&1
reg add "%KEY%" /v UninstallString /t REG_SZ /d "cmd.exe /c \"\"%DIR%\uninstall.bat\"\"" /f >nul 2>&1
reg add "%KEY%" /v EstimatedSize /t REG_DWORD /d 5200000 /f >nul 2>&1
reg add "%KEY%" /v NoModify /t REG_DWORD /d 1 /f >nul 2>&1
reg add "%KEY%" /v NoRepair /t REG_DWORD /d 1 /f >nul 2>&1

echo.
echo 安裝完成！之後點兩下 start.bat 就能使用。要移除的話點兩下 uninstall.bat。
pause
exit /b 0

:fail
echo.
echo 安裝沒有完成。請把這個視窗的內容截圖傳給 Claude，會幫你看是哪裡出問題。
pause
exit /b 1
