"""依這台電腦推薦設定（2.20.0）：看顯示卡、記憶體、CPU 核心數，建議語音和翻譯的設定。
只給建議，按了「套用」才會改；使用者自己的選擇不會被偷偷改掉。"""

import os
import sys

KEYS = ("quality", "speech_mode", "live_captions")


def ram_gb() -> float:
    """這台電腦有幾 GB 記憶體（查不到回傳 0）。"""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("ptotal", ctypes.c_ulonglong), ("pavail", ctypes.c_ulonglong),
                            ("vtotal", ctypes.c_ulonglong), ("vavail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total / 2**30, 1)
            return 0.0
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 2**20, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def suggest(gpu: dict, ram: float, cores: int) -> dict:
    """回傳 {"changes": {設定: 值}, "why": [原因代碼]}。原因代碼由介面翻成文字（rc_why_代碼）。
    顯示卡夠大：翻譯品質自動、語音用「比較準」、開即時字幕；一般顯示卡：自動、比較快、即時字幕；
    沒有可用的顯示卡：標準品質、比較快，核心少又記憶體小就關掉即時字幕（它會多吃一份算力）。"""
    tier = (gpu or {}).get("tier", "cpu")
    why: list[str] = []
    if tier == "high":
        changes = {"quality": "auto", "speech_mode": "accurate", "live_captions": True}
        why.append("gpu_high")
    elif tier == "fast":
        changes = {"quality": "auto", "speech_mode": "fast", "live_captions": True}
        why.append("gpu_fast")
    else:
        changes = {"quality": "standard", "speech_mode": "fast", "live_captions": cores >= 6 and (ram == 0 or ram >= 12)}
        why.append("cpu_only")
        if not changes["live_captions"]:
            why.append("weak_pc")
    if 0 < ram < 8:
        changes.update(quality="standard", speech_mode="fast", live_captions=False)
        why.append("low_ram")
    return {"changes": changes, "why": why}


def state(gpu: dict, current: dict) -> dict:
    """推薦的設定、現在的值、哪些不一樣。"""
    cores = os.cpu_count() or 2
    ram = ram_gb()
    out = suggest(gpu, ram, cores)
    out["current"] = {k: current.get(k) for k in KEYS}
    out["differ"] = [k for k in KEYS if out["changes"][k] != current.get(k)]
    out["pc"] = {"gpu": (gpu or {}).get("name") or "", "vram_gb": round(((gpu or {}).get("vram_mb") or 0) / 1024, 1), "ram_gb": ram, "cores": cores}
    return out
