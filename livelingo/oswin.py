"""只有 Windows 才用得到的小工具：剪貼簿、單一執行個體、讓視窗不被螢幕擷取拍到。"""

import sys
from pathlib import Path

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


def allow_foreground(pid: int) -> bool:
    """讓我們自己開的小程式（快速輸入框）可以把視窗放到最前面。只是給那個程式許可，不會動到別的視窗。"""
    if not IS_WIN:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.AllowSetForegroundWindow(int(pid)))
    except Exception:  # noqa: BLE001
        return False


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


def exclude_from_capture(hwnd: int, hide: bool = True) -> None:
    """
    hide=True：這個視窗不會被任何螢幕擷取拍到（Discord 分享螢幕、OBS 直播、錄影都看不到它；Windows 10 2004 以上）。
    hide=False：照常讓擷取拍得到。讀螢幕的功能自己會把字幕蓋住的地方遮掉（service.capture_mask），不會讀到自己顯示的字。
    """
    if not IS_WIN or not hwnd:
        return
    try:
        import ctypes

        ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), 0x11 if hide else 0)   # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
    except Exception:  # noqa: BLE001
        pass


def window_rect_of(hwnd: int) -> list[int] | None:
    """這個視窗現在在螢幕上的 [左, 上, 寬, 高]；看不見（隱藏、縮小）就是 None。"""
    if not IS_WIN or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        if not u32.IsWindowVisible(wintypes.HWND(hwnd)) or u32.IsIconic(wintypes.HWND(hwnd)):
            return None
        r = wintypes.RECT()
        if not u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
        return [r.left, r.top, r.right - r.left, r.bottom - r.top]
    except Exception:  # noqa: BLE001
        return None


def style_title_bar(hwnd: int, caption: str = "#171A1E", text: str = "#F2F4F6", dark: bool = True) -> bool:
    """把 Windows 的標題列改成和程式一樣的顏色，視窗從上到下才是同一套。顏色是 #RRGGBB。

    用的是 Windows 自己提供的視窗外觀設定（Windows 11 才能指定顏色；Windows 10 只會變成深色或淺色），
    拖曳、貼齊螢幕邊緣、改大小都還是 Windows 原本的行為。
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    def colorref(value: str) -> wintypes.DWORD:
        value = value.lstrip("#")
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        return wintypes.DWORD(r | (g << 8) | (b << 16))

    try:
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        ok = True
        on = ctypes.c_int(1 if dark else 0)
        set_attr(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))                  # 深色或淺色的標題列
        for attribute, value in ((35, colorref(caption)), (36, colorref(text)), (34, colorref(caption))):      # 底色、文字、外框
            ok = set_attr(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0 and ok
        return ok
    except Exception:  # noqa: BLE001
        return False


def work_area() -> tuple[int, int, int, int]:
    """主螢幕扣掉工作列之後的範圍 (left, top, width, height)。"""
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return 0, 0, 1920, 1040


_MONITOR_API = None


def _monitor_api():
    """
    問螢幕配置要用的 Windows 函式和資料型別，只準備一次。用自己的一份 user32（不跟別的模組共用參數設定），
    型別也固定同一個：之前每次呼叫都重新定義型別、設定在共用的那一份上，兩條執行緒同時問就會互相蓋掉而出錯。
    """
    global _MONITOR_API
    if _MONITOR_API is None:
        import ctypes
        from ctypes import wintypes

        class Info(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        dll = ctypes.WinDLL("user32")
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        dll.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        dll.GetMonitorInfoW.restype = wintypes.BOOL
        dll.EnumDisplayMonitors.argtypes = [wintypes.HANDLE, ctypes.c_void_p, callback, wintypes.LPARAM]
        dll.EnumDisplayMonitors.restype = wintypes.BOOL
        _MONITOR_API = (dll, Info, callback)
    return _MONITOR_API


def monitors() -> list[dict]:
    """
    接著的每一個螢幕：[{"left","top","width","height","work":[左,上,寬,高]（扣掉工作列）,"primary"}]，
    主螢幕排第一個，其他的由左到右。測試用：LIVELINGO_FAKE_MONITORS="0,0,1920,1080;1920,0,2560,1440"。
    """
    import os

    fake = os.environ.get("LIVELINGO_FAKE_MONITORS")
    found: list[dict] = []
    if fake is not None:
        for n, part in enumerate(p for p in fake.split(";") if p.strip()):
            left, top, width, height = (int(v) for v in part.split(","))
            found.append({"left": left, "top": top, "width": width, "height": height,
                          "work": [left, top, width, height - 40], "primary": n == 0})
    elif IS_WIN:
        import ctypes

        dll, info_type, callback_type = _monitor_api()

        def each(handle, _dc, _rect, _lparam):
            info = info_type()
            info.size = ctypes.sizeof(info_type)
            if dll.GetMonitorInfoW(handle, ctypes.byref(info)):
                m, w = info.monitor, info.work
                found.append({"left": m.left, "top": m.top, "width": m.right - m.left, "height": m.bottom - m.top,
                              "work": [w.left, w.top, w.right - w.left, w.bottom - w.top], "primary": bool(info.flags & 1)})
            return True

        try:
            dll.EnumDisplayMonitors(None, None, callback_type(each), 0)
        except Exception:  # noqa: BLE001
            found = []
    if not found:
        left, top, width, height = work_area()
        found = [{"left": 0, "top": 0, "width": max(width, 640), "height": max(height + 40, 480),
                  "work": [left, top, width, height], "primary": True}]
    return sorted(found, key=lambda m: (not m["primary"], m["left"], m["top"]))


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


def documents_dir() -> Path:
    """使用者的「文件」資料夾（可能被搬到 OneDrive，所以要問 Windows，不能自己猜）。"""
    if IS_WIN:
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(520)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:      # 5 = CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


# 「按住才說話」可以選的按鍵（名字: Windows 的虛擬鍵碼）。挑的是遊戲裡比較少用到、按著也不會打出字的鍵
PTT_KEYS = {"F8": 0x77, "F9": 0x78, "F10": 0x79, "CapsLock": 0x14, "RightCtrl": 0xA3, "RightAlt": 0xA5,
            "Mouse4": 0x05, "Mouse5": 0x06}


def key_down(name: str) -> bool:
    """這個鍵現在是不是按著（不管焦點在哪個視窗，遊戲全螢幕時也讀得到）。"""
    if not IS_WIN or name not in PTT_KEYS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(PTT_KEYS[name]) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


_dll_dirs: list = []


def add_dll_dirs(folders) -> None:
    """把資料夾加進「找 DLL 的地方」（語音辨識用顯示卡時，CUDA 和 cuDNN 的檔案在使用者的資料夾裡）。"""
    import os

    for folder in folders:
        folder = str(folder)
        if not IS_WIN or folder in _dll_dirs:
            continue
        _dll_dirs.append(folder)
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(folder)
        except OSError:
            pass


def open_path(path) -> bool:
    """用 Windows 預設的程式打開檔案或資料夾。"""
    if not IS_WIN:
        return False
    import os

    try:
        os.startfile(str(path))      # noqa: S606
        return True
    except OSError:
        return False


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
