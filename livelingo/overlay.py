"""
浮動留言：把翻譯像實況聊天室一樣直接顯示在遊戲畫面上。
它是一個透明、永遠在最上層、滑鼠會直接穿過去的普通視窗，疊在畫面上而已，不會碰遊戲本身。
（遊戲要用「無邊框」或「視窗」模式；獨佔全螢幕時 Windows 不讓任何視窗蓋在上面。）
"""

import os
import threading
import time

from . import chatpaint, oswin
from .config import DEFAULTS, MAX_LAYOUTS, OVERLAY_FORMS, OVERLAY_LOOK

MIN_SIZE = (240, 110)
FADE_IN, FADE_OUT = 0.22, 0.9
MAX_ITEMS = 40
NOTE_SECONDS = 3.0      # 程式自己的短提示停留幾秒
READ_START = 3.0        # 看到一則留言、眼睛移過去要的時間


def read_seconds(text: str) -> float:
    """邊打遊戲邊讀完這一句大概要幾秒：中文、韓文、日文一秒 5 個字，英文一秒 15 個字母（大約 3 個單字）。"""
    wide = sum(1 for ch in text if ord(ch) >= 0x1100)
    narrow = sum(1 for ch in text if ord(ch) < 0x1100 and not ch.isspace())
    return wide / 5 + narrow / 15


BAR_LOOK = {"overlay_align": "center", "overlay_grow": "up", "overlay_lines": 2}      # 底部字幕條固定的排法，其他外觀照設定


def monitor_at(index, monitors: list[dict] | None = None) -> dict:
    """第 index 個螢幕；沒有那麼多個（螢幕拔掉了）就用主螢幕。"""
    monitors = monitors or oswin.monitors()
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    return monitors[index] if 0 <= index < len(monitors) else monitors[0]


def monitor_of(rect, monitors: list[dict]) -> int:
    """這個位置主要在哪一個螢幕上（重疊面積最大的那個；完全不在任何螢幕上就是 0）。"""
    best, index = 0, 0
    for n, m in enumerate(monitors):
        area = _overlap(rect, (m["left"], m["top"], m["width"], m["height"]))
        if area > best:
            best, index = area, n
    return index


def _overlap(a, b) -> int:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def default_rect(monitor: dict | None = None) -> list[int]:
    """預設放在螢幕左側偏下，大約是遊戲聊天區上方的位置。"""
    left, top, width, height = (monitor or monitor_at(0))["work"]
    w = max(MIN_SIZE[0], min(620, int(width * 0.30)))
    h = max(MIN_SIZE[1], int(height * 0.30))
    return [left + 24, top + int(height * 0.36), w, h]


def bar_rect(monitor: dict, font: int, scale: float = 1.0) -> list[int]:
    """底部字幕條：螢幕下方置中的一條，高度剛好放兩行字。"""
    left, top, width, height = monitor["work"]
    w = max(MIN_SIZE[0], int(width * 0.72))
    h = max(MIN_SIZE[1], int(font * scale * 3.9) + 28)
    return [left + (width - w) // 2, top + height - h - max(24, int(height * 0.05)), w, h]


def fit_rect(rect, monitors: list[dict], keep: float = 0.5) -> list[int]:
    """
    位置還看得到（至少一半在某個螢幕上）就原樣回傳；不然搬回螢幕裡：
    挑重疊最多的螢幕（完全沒重疊就用主螢幕），太大就縮到放得下，再推進工作區。
    """
    rect = [int(v) for v in rect]
    area = max(1, rect[2] * rect[3])
    index = monitor_of(rect, monitors)
    m = monitors[index]
    if _overlap(rect, (m["left"], m["top"], m["width"], m["height"])) >= area * keep:
        return rect
    left, top, width, height = m["work"]
    w, h = min(rect[2], width), min(rect[3], height)
    x = min(max(rect[0], left), left + width - w)
    y = min(max(rect[1], top), top + height - h)
    return [x, y, w, h]


def move_to_monitor(rect, source: dict, target: dict) -> list[int]:
    """把位置搬到另一個螢幕上「同樣的相對位置」（兩個螢幕大小不同時照比例換算，大小不變但不超出去）。"""
    sl, st, sw, sh = source["work"]
    tl, tt, tw, th = target["work"]
    w, h = min(int(rect[2]), tw), min(int(rect[3]), th)
    x = tl + int((rect[0] - sl) / max(1, sw) * tw)
    y = tt + int((rect[1] - st) / max(1, sh) * th)
    return [min(max(x, tl), tl + tw - w), min(max(y, tt), tt + th - h), w, h]


# 一鍵版面：外觀（沒寫到的項目用預設值）和位置（主螢幕工作區的 左、上、寬、高 比例；None = 預設位置）
PRESETS = {
    "chat": {"look": {}, "place": None},
    "subtitle": {"look": {"overlay_align": "center", "overlay_names": False, "overlay_lines": 2, "overlay_font": 30,
                          "overlay_backdrop": 45, "overlay_seconds": 8},
                 "place": (0.18, 0.74, 0.64, 0.20)},
    "quiet": {"look": {"overlay_align": "right", "overlay_grow": "down", "overlay_font": 18, "overlay_opacity": 70,
                       "overlay_outline": 1, "overlay_lines": 4, "overlay_seconds": 8},
              "place": (0.70, 0.05, 0.28, 0.24)},
}


def preset_rect(name: str, monitor: dict | None = None) -> list[int]:
    place = PRESETS[name]["place"]
    monitor = monitor or monitor_at(0)
    if not place:
        return default_rect(monitor)
    left, top, width, height = monitor["work"]
    return [left + int(width * place[0]), top + int(height * place[1]),
            max(MIN_SIZE[0], int(width * place[2])), max(MIN_SIZE[1], int(height * place[3]))]


# ---------------------------------------------------------------------- 視窗（只有 Windows）

class MemoryWindow:
    """沒有真的視窗時用的替身（開發和測試）：只把最後一張圖記下來。"""

    def __init__(self, on_rect=None, on_done=None):
        self.image = None
        self.rect = None
        self.editing = False
        self.frames = 0
        self.scale = 1.0
        self.hwnd = 0
        self.hidden = False      # 是不是不讓螢幕分享拍到

    def start(self, rect) -> bool:
        self.rect = list(rect)
        return True

    def set_hidden(self, hidden: bool) -> None:
        self.hidden = bool(hidden)

    def paint(self, image, rect) -> None:
        self.image, self.rect = image, list(rect)
        self.frames += 1
        path = os.environ.get("LIVELINGO_OVERLAY_PNG")
        if path:
            try:
                image.save(path)
            except OSError:
                pass

    def set_edit(self, on: bool) -> None:
        self.editing = on

    def close(self) -> None:
        self.image = None


if oswin.IS_WIN:
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                    ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]

    _u32.DefWindowProcW.restype = LRESULT
    _u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.RegisterClassW.restype = wintypes.ATOM
    _u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _u32.CreateWindowExW.restype = wintypes.HWND
    _u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                     wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID]
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.DispatchMessageW.restype = LRESULT
    _u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.DestroyWindow.argtypes = [wintypes.HWND]
    _u32.PostQuitMessage.argtypes = [ctypes.c_int]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.GetDC.restype = wintypes.HDC
    _u32.GetDC.argtypes = [wintypes.HWND]
    _u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _u32.LoadCursorW.restype = wintypes.HANDLE
    _u32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    _u32.UpdateLayeredWindow.restype = wintypes.BOOL
    _u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                         ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                         wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    _u32.MonitorFromRect.restype = wintypes.HANDLE
    _u32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    _gdi.CreateCompatibleDC.restype = wintypes.HDC
    _gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi.CreateDIBSection.restype = wintypes.HANDLE
    _gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _gdi.SelectObject.restype = wintypes.HANDLE
    _gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    _gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    _gdi.DeleteDC.argtypes = [wintypes.HDC]
    _k32.GetModuleHandleW.restype = wintypes.HMODULE
    _k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    WS_POPUP = 0x80000000
    WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
    WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
    GWL_EXSTYLE = -20
    WM_DESTROY, WM_MOVE, WM_SIZE, WM_CLOSE = 0x02, 0x03, 0x05, 0x10
    WM_MOUSEACTIVATE, WM_GETMINMAXINFO, WM_NCHITTEST = 0x21, 0x24, 0x84
    WM_NCLBUTTONDBLCLK, WM_EXITSIZEMOVE = 0xA3, 0x232
    WM_PAINT_NOW, WM_SET_EDIT = 0x8001, 0x8002      # WM_APP + 1, + 2
    SWP_FLAGS = 0x0001 | 0x0002 | 0x0010            # NOSIZE | NOMOVE | NOACTIVATE
    HWND_TOPMOST = wintypes.HWND(-1)
    _CLASS = "LiveLingoFloatingChat"
    _registered = {}

    class LayeredWindow:
        """每個像素都能有自己透明度的視窗。自己開一條執行緒處理 Windows 的訊息。"""

        def __init__(self, on_rect=None, on_done=None):
            self.on_rect = on_rect or (lambda rect, final: None)
            self.on_done = on_done or (lambda: None)
            self.hwnd = 0
            self.editing = False
            self.scale = 1.0
            self.frames = 0
            self.error = ""
            self.hidden = False
            self._pending = None
            self._lock = threading.Lock()
            self._ready = threading.Event()
            self._thread = None

        # ---- 給外面呼叫的（任何執行緒）----

        def start(self, rect) -> bool:
            self._thread = threading.Thread(target=self._run, args=(list(rect),), daemon=True, name="overlay-window")
            self._thread.start()
            self._ready.wait(5)
            return bool(self.hwnd)

        def paint(self, image, rect) -> None:
            if not self.hwnd:
                return
            data = chatpaint.to_bgra_premultiplied(image)
            with self._lock:
                self._pending = (data, image.width, image.height, int(rect[0]), int(rect[1]))
            _u32.PostMessageW(self.hwnd, WM_PAINT_NOW, 0, 0)

        def set_edit(self, on: bool) -> None:
            if self.hwnd:
                _u32.PostMessageW(self.hwnd, WM_SET_EDIT, 1 if on else 0, 0)

        def set_hidden(self, hidden: bool) -> None:
            self.hidden = bool(hidden)
            if self.hwnd:
                oswin.exclude_from_capture(self.hwnd, self.hidden)

        def close(self) -> None:
            if self.hwnd:
                _u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(2)

        def window_rect(self) -> list[int]:
            r = wintypes.RECT()
            _u32.GetWindowRect(self.hwnd, ctypes.byref(r))
            return [r.left, r.top, r.right - r.left, r.bottom - r.top]

        # ---- 視窗自己的執行緒 ----

        def _run(self, rect) -> None:
            try:
                instance = _k32.GetModuleHandleW(None)
                if _CLASS not in _registered:
                    cls = WNDCLASSW()
                    cls.lpfnWndProc = WNDPROC(self._dispatch)
                    cls.hInstance = instance
                    cls.hCursor = _u32.LoadCursorW(None, ctypes.c_void_p(32512))      # 一般的箭頭
                    cls.lpszClassName = _CLASS
                    if not _u32.RegisterClassW(ctypes.byref(cls)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    _registered[_CLASS] = cls      # 連同裡面的函式一起留著
                _windows.append(self)
                style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                hwnd = _u32.CreateWindowExW(style, _CLASS, "LiveLingo", WS_POPUP, int(rect[0]), int(rect[1]),
                                            int(rect[2]), int(rect[3]), None, None, instance, None)
                if not hwnd:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.hwnd = hwnd
                try:
                    self.scale = max(1.0, _u32.GetDpiForWindow(wintypes.HWND(hwnd)) / 96.0)
                except Exception:  # noqa: BLE001  （Windows 10 很舊的版本沒有這個函式）
                    self.scale = 1.0
                oswin.exclude_from_capture(hwnd, self.hidden)      # 預設讓螢幕分享拍得到；讀螢幕的功能自己會把這塊遮掉
                _u32.ShowWindow(hwnd, 4)              # SW_SHOWNOACTIVATE
            except Exception as ex:  # noqa: BLE001
                self.error = repr(ex)
                self._ready.set()
                return
            self._ready.set()
            msg = wintypes.MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
            self.hwnd = 0
            if self in _windows:
                _windows.remove(self)

        @staticmethod
        def _dispatch(hwnd, message, wparam, lparam):
            for window in list(_windows):
                if window.hwnd == hwnd or not window.hwnd:
                    return window._wndproc(hwnd, message, wparam, lparam)
            return _u32.DefWindowProcW(hwnd, message, wparam, lparam)

        def _wndproc(self, hwnd, message, wparam, lparam):
            try:
                if message == WM_PAINT_NOW:
                    self._blit(hwnd)
                    return 0
                if message == WM_SET_EDIT:
                    self._apply_edit(hwnd, bool(wparam))
                    return 0
                if message == WM_NCHITTEST:
                    return self._hit(hwnd, lparam) if self.editing else -1      # HTTRANSPARENT
                if message == WM_MOUSEACTIVATE and not self.editing:
                    return 3                                                   # MA_NOACTIVATE
                if message == WM_GETMINMAXINFO:
                    info = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                    info.ptMinTrackSize.x = int(MIN_SIZE[0] * self.scale)
                    info.ptMinTrackSize.y = int(MIN_SIZE[1] * self.scale)
                    return 0
                if message in (WM_MOVE, WM_SIZE) and self.editing:
                    self.on_rect(self.window_rect(), False)
                    return 0
                if message == WM_EXITSIZEMOVE and self.editing:
                    self.on_rect(self.window_rect(), True)
                    return 0
                if message == WM_NCLBUTTONDBLCLK and self.editing:
                    threading.Thread(target=self.on_done, daemon=True).start()
                    return 0
                if message == WM_CLOSE:
                    _u32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    _u32.PostQuitMessage(0)
                    return 0
            except Exception as ex:  # noqa: BLE001
                self.error = repr(ex)
            return _u32.DefWindowProcW(hwnd, message, wparam, lparam)

        def _hit(self, hwnd, lparam) -> int:
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            r = wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            grip = int(16 * self.scale)
            left, right = x - r.left < grip, r.right - x <= grip
            top, bottom = y - r.top < grip, r.bottom - y <= grip
            if top:
                return 13 if left else 14 if right else 12
            if bottom:
                return 16 if left else 17 if right else 15
            return 10 if left else 11 if right else 2      # 中間 = 抓著移動

        def _apply_edit(self, hwnd, on: bool) -> None:
            self.editing = on
            style = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if on:
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            else:
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
            _u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS | 0x0020)      # FRAMECHANGED

        def _blit(self, hwnd) -> None:
            with self._lock:
                pending, self._pending = self._pending, None
            if not pending:
                return
            data, width, height, x, y = pending
            if self.editing:
                # 使用者正在拖：位置和大小以視窗現在的樣子為準，尺寸對不上的舊圖就丟掉
                rect = self.window_rect()
                if (rect[2], rect[3]) != (width, height):
                    return
                x, y = rect[0], rect[1]
            screen = _u32.GetDC(None)
            memory = _gdi.CreateCompatibleDC(screen)
            header = BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            header.biWidth, header.biHeight = width, -height      # 負的高度 = 由上往下
            header.biPlanes, header.biBitCount = 1, 32
            bits = ctypes.c_void_p()
            bitmap = _gdi.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            try:
                if not bitmap or not bits:
                    self.error = "CreateDIBSection failed"
                    return
                ctypes.memmove(bits, data, len(data))
                old = _gdi.SelectObject(memory, bitmap)
                blend = BLENDFUNCTION(0, 0, 255, 1)      # AC_SRC_OVER，用圖本身的透明度
                ok = _u32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                              ctypes.byref(wintypes.SIZE(width, height)), memory,
                                              ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
                _gdi.SelectObject(memory, old)
                if ok:
                    self.frames += 1
                    self.error = ""
                else:
                    self.error = f"UpdateLayeredWindow failed ({ctypes.get_last_error()})"
                if self.frames % 20 == 1:      # 偶爾再排到最上層一次，免得被別的置頂視窗蓋住
                    _u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
            finally:
                if bitmap:
                    _gdi.DeleteObject(bitmap)
                _gdi.DeleteDC(memory)
                _u32.ReleaseDC(None, screen)

    _windows: list = []
else:
    LayeredWindow = None

# ---------------------------------------------------------------------- 控制

class Overlay:
    """決定現在要顯示哪幾則留言、各自多透明，畫好之後交給視窗。"""

    def __init__(self, service, window_class=None):
        self.service = service
        self.window_class = window_class or LayeredWindow or MemoryWindow
        self.window = None
        self.editing = False
        self.items: list[dict] = []
        self.rect = None
        self._tiles: dict = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._stop = threading.Event()
        self._last = None
        self._screens = None          # 上一次看到的螢幕配置（變了要檢查留言還在不在畫面上）
        self._told = 0.0

    # ---- 開關 ----

    @property
    def running(self) -> bool:
        return bool(self.window)

    def apply(self) -> None:
        """設定改了之後呼叫：該開就開、該關就關，其他的改動下一次畫的時候生效。"""
        want = bool(self.service.cfg["overlay"]) and not self.service.closing.is_set()
        if want and not self.window:
            self._open()
        elif not want and self.window:
            self._close()
        if self.window:
            if self.bar and self.editing:      # 字幕條的位置是自動的，不能拖
                self.set_edit(False)
            if not self.editing:               # 正在拖的時候不要把它拉回存檔裡的舊位置
                self.rect = self._home()
        self._tiles.clear()
        self._last = None
        self._wake.set()

    def apply_share(self) -> None:
        """「讓分享螢幕看得到」改了：開著的視窗馬上換。"""
        if self.window:
            self.window.set_hidden(not self.service.cfg["share_visible"])

    def covers(self) -> list[int] | None:
        """現在有留言蓋在螢幕上的哪裡 [左, 上, 寬, 高]（讀螢幕時要把這塊遮掉）；沒有就是 None。"""
        with self._lock:
            shown = bool(self.items)
        if self.window and self.rect and (shown or self.editing):
            return list(self.rect)
        return None

    @property
    def bar(self) -> bool:
        return self.service.cfg["overlay_form"] == "bar"

    def _home(self, monitors: list[dict] | None = None) -> list[int]:
        """現在應該在哪：字幕條照螢幕算；浮動留言用存起來的位置，跑到看不見的地方就拉回來。"""
        cfg = self.service.cfg
        monitors = monitors or oswin.monitors()
        if self.bar:
            scale = getattr(self.window, "scale", 1.0) or 1.0
            return bar_rect(monitor_at(cfg["overlay_monitor"], monitors), int(cfg["overlay_font"]), scale)
        rect = cfg["overlay_rect"]
        if not (isinstance(rect, list) and len(rect) == 4):
            return default_rect(monitor_at(cfg["overlay_monitor"], monitors))
        return fit_rect(rect, monitors)

    def check_screens(self, monitors: list[dict] | None = None) -> bool:
        """
        螢幕配置變了（拔掉一個螢幕、換了解析度）就重新算位置；浮動留言被拉回來時存起來並通知。
        回傳位置有沒有變。平常每隔幾秒呼叫一次。
        """
        monitors = monitors or oswin.monitors()
        shape = [(m["left"], m["top"], m["width"], m["height"]) for m in monitors]
        if shape == self._screens or self.editing or not self.rect:
            self._screens = shape
            return False
        first, self._screens = self._screens is None, shape
        before, self.rect = list(self.rect), self._home(monitors)
        if self.rect == before:
            return False
        if not self.bar:
            self.service.cfg.set(overlay_rect=list(self.rect), overlay_monitor=monitor_of(self.rect, monitors))
            self.service.hub.publish("config", self.service.cfg.snapshot())
            if not first:
                self.service.notice("overlay_pulled_back")
        self._last = None
        self._wake.set()
        return True

    def _open(self) -> None:
        self.rect = self._home()
        window = self.window_class(on_rect=self._moved, on_done=lambda: self.set_edit(False))
        window.hidden = not self.service.cfg["share_visible"]      # 視窗建好（start）的時候就要用到
        if not window.start(self.rect):
            self.service.log(f"floating chat window failed: {getattr(window, 'error', '')}")
            self.service.cfg.set(overlay=False)
            self.service.hub.publish("config", self.service.cfg.snapshot())
            self.service.notice("overlay_failed")
            return
        self.window = window
        self.rect = self._home()      # 視窗開了才知道螢幕的放大比例（字幕條的高度要用）
        self._screens = None
        self._stop.clear()
        self._last = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="overlay")
        self._thread.start()

    def _close(self) -> None:
        self._stop.set()
        self._wake.set()
        window, self.window = self.window, None
        if self.editing:
            self.editing = False
            self.service.hub.publish("overlay", self.state())
        if window:
            window.close()

    def stop(self) -> None:
        self._close()

    def state(self) -> dict:
        return {"editing": self.editing, "running": self.running, "rect": list(self.rect) if self.rect else None}

    def screens(self) -> dict:
        """設定頁的縮小螢幕圖要的東西：每個螢幕在哪、留言現在在哪。"""
        monitors = oswin.monitors()
        rect = list(self.rect) if self.window and self.rect else self._home(monitors)
        return {"ok": True, "monitors": monitors, "rect": rect, "form": self.service.cfg["overlay_form"],
                "monitor": monitor_of(rect, monitors), "min": list(MIN_SIZE)}

    # ---- 留言 ----

    def on_message(self, msg: dict) -> None:
        if msg.get("pending") or msg.get("error") or not msg.get("translated"):
            return
        kind = "me" if msg.get("who") == "me" else ("screen" if msg.get("via") == "screen" else "heard")
        if kind == "me" and not self.service.cfg["overlay_mine"]:
            return
        self.push(kind, msg["translated"], msg.get("original", ""), key=msg["id"], who=self._who(msg), mention=bool(msg.get("mention")))

    def _who(self, msg: dict) -> tuple | None:
        """這一則是第幾個說話者、叫什麼名字（有開「分辨說話者」而且分得出來才有）。"""
        tag = getattr(self.service, "speaker_tag", None)
        return tag(msg) if tag else None

    def on_partial(self, msg: dict, text: str) -> None:
        """還在翻的句子：對方說的話先把翻到一半的譯文顯示出來（翻完會換成整句）。螢幕上讀到的要翻完才知道值不值得顯示，不先出。"""
        if msg.get("via") != "heard" or not text.strip():
            return
        self.push("heard", text, msg.get("original", ""), key=msg["id"], who=self._who(msg))

    def push(self, kind: str, text: str, original: str = "", key=None, life: float = 0.0, who: tuple | None = None, mention: bool = False) -> None:
        """life：這一則自己停留幾秒（0 = 照設定的停留時間）。who：（第幾個說話者, 取的名字），名字用那個人的顏色。"""
        with self._lock:
            now = time.monotonic()
            for item in self.items:
                if key is not None and item["key"] == key:      # 同一則（例如按了「翻錯了」重翻）：換掉內容
                    item.update(text=text, original=original, at=now - FADE_IN, version=item["version"] + 1, who=who or item.get("who"), mention=mention)
                    break
            else:
                self.items.append({"key": key if key is not None else f"x{now}", "kind": kind, "text": text,
                                   "original": original, "at": now, "version": 0, "life": float(life), "who": who, "mention": mention})
                del self.items[:-MAX_ITEMS]
        self._wake.set()

    def note(self, text: str) -> None:
        """程式自己的短提示（按了快速鍵之後告訴你發生了什麼事）。幾秒就消失，新的會換掉舊的。"""
        self.on_removed("note")
        self.push("note", text, key="note", life=NOTE_SECONDS)

    def on_removed(self, key) -> None:
        with self._lock:
            self.items = [i for i in self.items if i["key"] != key]
        self._wake.set()

    def clear(self) -> None:
        with self._lock:
            self.items.clear()
        self._wake.set()

    def test(self) -> None:
        """顯示幾則範例，讓人不用等到真的有人說話就能看到樣子。"""
        lang = self.service.cfg["ui_lang"] or "en"
        for kind, text in chatpaint.SAMPLES.get(lang, chatpaint.SAMPLES["en"]):
            self.push(kind, text)
            time.sleep(0.35)

    # ---- 調整位置 ----

    def set_edit(self, on: bool) -> None:
        if not self.window or self.bar:
            on = False
        if on == self.editing:
            return
        self.editing = on
        if self.window:
            self.window.set_edit(on)
        if not on and self.rect and not self.bar:
            self._save_rect()
        self.service.hub.publish("overlay", self.state())
        self._last = None
        self._wake.set()

    def _save_rect(self, what: str = "overlay_rect") -> bool:
        """把現在的位置存起來（連同它在哪個螢幕），並讓介面顯示「已儲存」。"""
        ok = self.service.cfg.set(overlay_rect=list(self.rect), overlay_monitor=monitor_of(self.rect, oswin.monitors()))
        self.service.hub.publish("config", self.service.cfg.snapshot())
        self.service.stored(what, ok)
        return ok

    def reload(self) -> None:
        """設定檔裡的位置被整個換掉了（匯入設定、恢復預設、復原）：照新的重新擺。"""
        if self.window:
            self.rect = self._home()
            self._last = None
            self._wake.set()
            self.service.hub.publish("overlay", self.state())

    def reset_position(self) -> None:
        if self.bar:
            return
        self.rect = default_rect(monitor_at(self.service.cfg["overlay_monitor"]))
        self._save_rect()
        self._last = None
        self._wake.set()

    def place(self, rect, final: bool = True) -> dict:
        """設定頁的螢幕圖上拖方框：留言直接搬到那裡（final：放開滑鼠，這才存）。字幕條不能擺。"""
        try:
            rect = [int(v) for v in rect]
            assert len(rect) == 4
        except Exception:  # noqa: BLE001
            return {"ok": False}
        if self.bar:
            return {"ok": False, "rect": self.rect}
        rect[2], rect[3] = max(MIN_SIZE[0], rect[2]), max(MIN_SIZE[1], rect[3])
        self.rect = fit_rect(rect, oswin.monitors())
        saved = self._save_rect("overlay_map") if final else False
        self._last = None
        self._wake.set()
        return {"ok": True, "saved": saved, "rect": list(self.rect)}

    def choose_monitor(self, index: int) -> dict:
        """換一個螢幕顯示：字幕條到那個螢幕的下方；浮動留言搬到那個螢幕上同樣的相對位置。"""
        monitors = oswin.monitors()
        if not 0 <= int(index) < len(monitors):
            return {"ok": False}
        index = int(index)
        if not self.bar:
            current = self.rect or self._home(monitors)
            rect = move_to_monitor(current, monitors[monitor_of(current, monitors)], monitors[index])
            self.service.cfg.set(overlay_rect=rect)
        saved = self.service.cfg.set(overlay_monitor=index)
        self.rect = self._home(monitors) if self.window or not self.bar else self.rect
        self.service.hub.publish("config", self.service.cfg.snapshot())
        self.service.stored("overlay_map", saved)
        self._last = None
        self._wake.set()
        return {"ok": True, "saved": saved, "rect": self.rect}

    # ---- 自己存的版面 ----

    def save_layout(self, name: str) -> dict:
        """把現在的外觀、位置、顯示方式存成一組，之後一鍵換回來。同名的會蓋掉；最多 MAX_LAYOUTS 組。"""
        name = " ".join(str(name or "").split())[:24]
        cfg = self.service.cfg.snapshot()
        layouts = [dict(item) for item in cfg["overlay_layouts"]]
        if not name:
            return {"ok": False, "error": "name"}
        entry = {"name": name, "look": {k: cfg[k] for k in OVERLAY_LOOK}, "rect": list(self.rect or self._home()),
                 "form": cfg["overlay_form"], "monitor": cfg["overlay_monitor"]}
        index = next((n for n, item in enumerate(layouts) if item.get("name") == name), None)
        if index is None:
            if len(layouts) >= MAX_LAYOUTS:
                return {"ok": False, "error": "full"}
            layouts.append(entry)
            index = len(layouts) - 1
        else:
            layouts[index] = entry
        saved = self.service.cfg.set(overlay_layouts=layouts)
        self.service.hub.publish("config", self.service.cfg.snapshot())
        return {"ok": True, "saved": saved, "index": index}

    def apply_layout(self, index) -> dict:
        layouts = self.service.cfg["overlay_layouts"]
        try:
            entry = layouts[int(index)]
        except (TypeError, ValueError, IndexError):
            return {"ok": False}
        look = {k: v for k, v in (entry.get("look") or {}).items() if k in OVERLAY_LOOK}
        form = entry.get("form") if entry.get("form") in OVERLAY_FORMS else "float"
        rect = entry.get("rect")
        placed = True
        if isinstance(rect, list) and len(rect) == 4:
            placed = self.service.cfg.set(overlay_rect=fit_rect(rect, oswin.monitors()))
        result = self.service.update_config({**look, "overlay_form": form, "overlay_monitor": entry.get("monitor", 0),
                                             "overlay": True})
        self.rect = self._home() if self.window else self.rect
        self.service.hub.publish("config", self.service.cfg.snapshot())
        self._last = None
        self._wake.set()
        return {"ok": True, **result, "saved": bool(result["saved"] and placed)}

    def remove_layout(self, index) -> dict:
        layouts = [dict(item) for item in self.service.cfg["overlay_layouts"]]
        try:
            layouts.pop(int(index))
        except (TypeError, ValueError, IndexError):
            return {"ok": False}
        saved = self.service.cfg.set(overlay_layouts=layouts)
        self.service.hub.publish("config", self.service.cfg.snapshot())
        return {"ok": True, "saved": saved}

    def apply_preset(self, name: str) -> dict | None:
        """套用一個一鍵版面：外觀整組換掉、搬到那個版面的位置。回傳和 update_config 一樣；沒有這個版面回傳 None。"""
        if name not in PRESETS:
            return None
        self.rect = preset_rect(name, monitor_at(self.service.cfg["overlay_monitor"]))
        placed = self.service.cfg.set(overlay_rect=list(self.rect))
        result = self.service.update_config({**{k: DEFAULTS[k] for k in OVERLAY_LOOK}, **PRESETS[name]["look"],
                                             "overlay_form": "float"})
        self.rect = self._home() if self.window else self.rect
        self._last = None
        self._wake.set()
        return {**result, "saved": bool(result["saved"] and placed)}

    def _moved(self, rect, final: bool) -> None:
        """使用者在真的螢幕上拖：設定頁的螢幕圖跟著動（一秒最多通知十次）。"""
        self.rect = [int(v) for v in rect]
        if final:
            self.service.cfg.set(overlay_rect=list(self.rect), overlay_monitor=monitor_of(self.rect, oswin.monitors()))
        now = time.monotonic()
        if final or now - self._told > 0.1:
            self._told = now
            self.service.hub.publish("overlay", self.state())
        self._wake.set()

    # ---- 畫 ----

    @staticmethod
    def _life(item: dict, life: float) -> float:
        """這一則停留幾秒：自己有指定就用自己的（程式的短提示），不然照設定；0 = 不會自己消失。
        設定的秒數是「最少」停留多久：長的句子來不及讀完，所以照長度多留一下（最多到設定的三倍再加 5 秒）。"""
        own = float(item.get("life") or 0.0)
        if own or life <= 0:
            return own or 0.0
        need = READ_START + 1.5 * read_seconds(str(item.get("text") or ""))
        return max(life, min(need, 3 * life + 5))

    def _alpha(self, item: dict, now: float, life: float) -> float:
        age = now - item["at"]
        if age < FADE_IN:
            return max(0.05, age / FADE_IN)
        if life <= 0:
            return 1.0
        left = life - age
        if left <= 0:
            return 0.0
        return min(1.0, left / FADE_OUT)

    def _tile(self, item: dict, width: int, size: int, cfg: dict):
        style = chatpaint.style_of(cfg)
        key = (item["key"], item["version"], bool(item.get("mention")), width, size, cfg["overlay_original"], cfg["overlay_backdrop"],
               cfg["ui_lang"], cfg["my_lang"], cfg["their_lang"], chatpaint.style_key(style))
        tile = self._tiles.get(key)
        if tile is None:
            names = chatpaint.NAMES.get(cfg["ui_lang"] or "en", chatpaint.NAMES["en"])
            original = item["original"] if cfg["overlay_original"] and item["original"] != item["text"] else ""
            name, tint = names.get(item["kind"], chatpaint.NOTE_NAME), None
            if item.get("who"):      # 分得出是誰：名字換成那個人的，顏色也是他的
                name, tint = chatpaint.speaker_name(item["who"][0], cfg["ui_lang"] or "en", item["who"][1]), chatpaint.speaker_color(item["who"][0])
            if item.get("mention"):      # 提到我：名字前面加 @、換成紅色，在一排字幕裡一眼就看得到
                name, tint = "@" + name, chatpaint.MENTION_COLOR
            tile = chatpaint.paint_message(item["kind"], name, item["text"], width, size,
                                           original=original, backdrop=int(cfg["overlay_backdrop"]),
                                           han=chatpaint.han_kind(cfg["my_lang"], cfg["their_lang"]), style=style, name_color=tint)
            if len(self._tiles) > 120:
                self._tiles.clear()
            self._tiles[key] = tile
        return tile

    def frame(self, now: float | None = None):
        """回傳 (圖, 下一次要重畫前可以等幾秒)。圖和上一次一樣時回傳 (None, 秒數)。"""
        now = time.monotonic() if now is None else now
        cfg = self.service.cfg.snapshot()
        if cfg["overlay_form"] == "bar":
            cfg.update(BAR_LOOK)
        window = self.window
        scale = getattr(window, "scale", 1.0) or 1.0
        size = max(12, round(int(cfg["overlay_font"]) * scale))
        life = 0.0 if self.editing else float(cfg["overlay_seconds"])
        width, height = max(40, self.rect[2]), max(40, self.rect[3])
        with self._lock:
            self.items = [i for i in self.items if not self._life(i, life) or now - i["at"] < self._life(i, life)]
            items = list(self.items)
        if self.editing and not items:
            lang = cfg["ui_lang"] or "en"
            items = [{"key": f"sample{n}", "kind": kind, "text": text, "original": "", "at": now - 5, "version": 0}
                     for n, (kind, text) in enumerate(chatpaint.SAMPLES.get(lang, chatpaint.SAMPLES["en"]))]
        alphas = [1.0 if self.editing else self._alpha(i, now, self._life(i, life)) for i in items]
        moving = any(a < 0.999 for a in alphas)
        wait = 0.04 if moving else 1.0
        ends = [self._life(i, life) - FADE_OUT - (now - i["at"]) for i in items if self._life(i, life)]
        if ends and not moving:
            wait = max(0.04, min(1.0, min(ends)))
        signature = (tuple((i["key"], i["version"], round(a, 2)) for i, a in zip(items, alphas)), tuple(self.rect),
                     size, self.editing, cfg["overlay_original"], cfg["overlay_backdrop"], cfg["ui_lang"],
                     chatpaint.style_key(chatpaint.style_of(cfg)))
        if signature == self._last:
            return None, wait
        self._last = signature
        tiles = [(self._tile(i, width, size, cfg), a) for i, a in zip(items, alphas)]
        image = chatpaint.compose(tiles, (width, height), grow=cfg["overlay_grow"], opacity=int(cfg["overlay_opacity"]),
                                  lines=int(cfg["overlay_lines"]))
        if self.editing:
            image = chatpaint.edit_frame(image, cfg["ui_lang"] or "en", size)
        return image, wait

    def _loop(self) -> None:
        window = self.window
        checked = 0.0
        while not self._stop.is_set() and not self.service.closing.is_set():
            try:
                if time.monotonic() - checked > 3.0:      # 螢幕配置有沒有變
                    checked = time.monotonic()
                    self.check_screens()
                image, wait = self.frame()
                if image is not None and self.window is window:
                    window.paint(image, self.rect)
            except Exception as ex:  # noqa: BLE001
                self.service.log(f"floating chat paint failed: {ex!r}")
                wait = 1.0
            self._wake.wait(wait)
            self._wake.clear()
