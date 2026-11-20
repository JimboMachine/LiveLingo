"""只有 Windows 才用得到的小工具：剪貼簿、單一執行個體、讓視窗不被螢幕擷取拍到。"""

import sys

IS_WIN = sys.platform == "win32"
MUTEX_NAME = "LiveLingoRunning"   # 安裝程式用這個名字判斷 LiveLingo 是不是還開著
_mutex = None
_last_copied = ""


def copy_text(text: str) -> bool:
    """把文字放進剪貼簿。"""
    global _last_copied
    _last_copied = text
    if not IS_WIN:
        return True
    import ctypes
    from ctypes import wintypes

    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    data = text.encode("utf-16-le") + b"\x00\x00"
    import time

    for _ in range(10):   # 剪貼簿可能剛好被別的程式佔用，多試幾次
        if u32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        return False
    try:
        u32.EmptyClipboard()
        handle = k32.GlobalAlloc(0x0002, len(data))   # GMEM_MOVEABLE
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(handle)
        return bool(u32.SetClipboardData(13, handle))   # CF_UNICODETEXT
    finally:
        u32.CloseClipboard()


def last_copied() -> str:
    return _last_copied


def read_clipboard() -> str:
    """讀出剪貼簿裡的文字（自我檢查用）。"""
    if not IS_WIN:
        return _last_copied
    import ctypes
    from ctypes import wintypes

    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    if not u32.OpenClipboard(None):
        return ""
    try:
        handle = u32.GetClipboardData(13)
        if not handle:
            return ""
        ptr = k32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            k32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


def acquire_single_instance() -> bool:
    """第一個開的回傳 True；已經有一個在跑就回傳 False。"""
    global _mutex
    if not IS_WIN:
        return True
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    ctypes.set_last_error(0)
    _mutex = k32.CreateMutexW(None, 0, MUTEX_NAME)
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


def release_single_instance() -> None:
    global _mutex
    if IS_WIN and _mutex:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_mutex))
        _mutex = None


def exclude_from_capture(hwnd: int) -> None:
    """讓這個視窗不會被自己的螢幕擷取拍進去（Windows 10 2004 以上）。"""
    if not IS_WIN or not hwnd:
        return
    try:
        import ctypes

        ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), 0x11)   # WDA_EXCLUDEFROMCAPTURE
    except Exception:  # noqa: BLE001
        pass


def work_area() -> tuple[int, int, int, int]:
    """主螢幕扣掉工作列之後的範圍 (left, top, width, height)。"""
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return 0, 0, 1920, 1040


def has_webview2() -> bool:
    """這台電腦有沒有 WebView2（Windows 11 內建，Windows 10 幾乎都有）。沒有的話改用 Edge 開介面。"""
    if not IS_WIN:
        return True
    import winreg

    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, base in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\"), (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\"),
                       (winreg.HKEY_CURRENT_USER, "SOFTWARE\\")):
        try:
            with winreg.OpenKey(root, base + client) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


_MIC_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def mic_privacy_blocked() -> bool | None:
    """Windows 的「麥克風隱私權」是不是把桌面程式擋掉了。被擋的時候麥克風打得開，但收到的全是靜音。
    True = 擋住了，False = 沒擋，None = 看不出來（不是 Windows，或讀不到設定）。只讀取設定，不會更改。"""
    if not IS_WIN:
        return None
    import winreg

    seen = False
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (_MIC_CONSENT, _MIC_CONSENT + r"\NonPackaged"):      # 整個麥克風的開關、桌面程式的開關
            try:
                with winreg.OpenKey(root, sub) as key:
                    value = str(winreg.QueryValueEx(key, "Value")[0])
            except OSError:
                continue
            seen = True
            if value.lower() == "deny":
                return True
    return False if seen else None


def open_mic_settings() -> bool:
    """打開 Windows 設定裡的「麥克風隱私權」那一頁。"""
    if not IS_WIN:
        return False
    import os

    try:
        os.startfile("ms-settings:privacy-microphone")      # noqa: S606
        return True
    except OSError:
        return False
