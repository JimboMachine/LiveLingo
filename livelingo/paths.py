"""檔案放哪裡：程式本身在安裝資料夾，模型、設定和紀錄放在使用者的資料夾。"""

import os
import sys
from pathlib import Path

APP_NAME = "LiveLingo"
FROZEN = bool(getattr(sys, "frozen", False))
IS_WIN = sys.platform == "win32"


def data_dir() -> Path:
    """設定、詞彙表、模型、翻譯引擎、紀錄檔。卸載時會問要不要把設定、詞彙表、歷史留下來；模型和引擎一律刪掉。"""
    if os.environ.get("LIVELINGO_DATA"):
        p = Path(os.environ["LIVELINGO_DATA"])
    elif IS_WIN:
        p = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    else:
        p = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_dir() -> Path:
    """跟著程式一起安裝的檔案（介面的網頁、內建用語表）。"""
    return Path(__file__).resolve().parent


def self_command() -> list[str]:
    """用來再開一個自己（框選聊天區的小程式）的指令。"""
    if FROZEN:
        return [sys.executable]
    return [sys.executable, "-m", "livelingo"]
