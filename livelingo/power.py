"""筆電省電模式（2.21.0）：看電腦是不是靠電池在用。靠電池的時候，服務會把比較耗電的設定先換成省電的（用完插上電再換回來）。"""

import sys
from pathlib import Path

SAVER_SET = {"speech_mode": "fast", "live_captions": False, "denoise": False}      # 靠電池時換成這樣
SAVER_SLOW = 3.0                                                                  # 讀螢幕的間隔拉長幾倍


def on_battery() -> bool | None:
    """True = 正在靠電池；False = 有插電（或桌機）；None = 查不到。"""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte), ("saver", ctypes.c_ubyte),
                            ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

            status = Status()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return None
            if status.flag == 128:      # 沒有電池（桌機）
                return False
            return {0: True, 1: False}.get(status.ac)
        found = False
        for supply in sorted(Path("/sys/class/power_supply").glob("BAT*")):
            found = True
            if (supply / "status").read_text(encoding="utf-8").strip().lower() == "discharging":
                return True
        return False if found else None
    except Exception:  # noqa: BLE001
        return None
