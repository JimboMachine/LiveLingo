"""看這台電腦有沒有 NVIDIA 顯示卡，決定翻譯要用哪一種模式。"""

import os
import shutil
import subprocess
import sys

HIGH_VRAM_MB = 11000   # RTX 4070（桌機版 12 GB）以上：大模型整個放進顯示卡
FAST_VRAM_MB = 5500    # 6 GB 以上：標準模型放進顯示卡，只是比較快


def _nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if sys.platform == "win32":
        for base in (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
                     os.environ.get("ProgramFiles", r"C:\Program Files") + r"\NVIDIA Corporation\NVSMI"):
            p = os.path.join(base, "nvidia-smi.exe")
            if os.path.exists(p):
                return p
    return None


def _query() -> str:
    fake = os.environ.get("LIVELINGO_FAKE_GPU")   # 測試用，例如 "NVIDIA GeForce RTX 4070, 12282"
    if fake is not None:
        return fake
    exe = _nvidia_smi()
    if not exe:
        return ""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=flags, stdin=subprocess.DEVNULL)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def detect() -> dict:
    """回傳 {"name": 顯示卡名稱或 None, "vram_mb": 記憶體, "tier": "high" / "fast" / "cpu"}。"""
    best = {"name": None, "vram_mb": 0}
    for line in _query().splitlines():
        parts = [p.strip() for p in line.rsplit(",", 1)]
        if len(parts) != 2:
            continue
        try:
            vram = int(float(parts[1]))
        except ValueError:
            continue
        if vram > best["vram_mb"]:
            best = {"name": parts[0], "vram_mb": vram}
    if best["vram_mb"] >= HIGH_VRAM_MB:
        best["tier"] = "high"
    elif best["vram_mb"] >= FAST_VRAM_MB:
        best["tier"] = "fast"
    else:
        best["tier"] = "cpu"
    return best


def choose_mode(quality: str, gpu: dict) -> dict:
    """
    照設定和顯示卡決定實際要用的組合。
    回傳 {"model": "4b"/"12b", "engine": "cpu"/"cuda", "quality": "standard"/"high"}。
    """
    tier = gpu.get("tier", "cpu")
    if quality == "high" and tier == "high" or quality == "auto" and tier == "high":
        return {"model": "12b", "engine": "cuda", "quality": "high"}
    if tier in ("high", "fast"):
        return {"model": "4b", "engine": "cuda", "quality": "standard"}
    return {"model": "4b", "engine": "cpu", "quality": "standard"}
