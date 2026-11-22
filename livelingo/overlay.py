"""
浮動留言：把翻譯像實況聊天室一樣直接顯示在遊戲畫面上。
它是一個透明、永遠在最上層、滑鼠會直接穿過去的普通視窗，疊在畫面上而已，不會碰遊戲本身。
（遊戲要用「無邊框」或「視窗」模式；獨佔全螢幕時 Windows 不讓任何視窗蓋在上面。）
"""

import os
import threading
import time

from . import chatpaint, oswin
from .config import DEFAULTS, OVERLAY_LOOK

MIN_SIZE = (240, 110)
FADE_IN, FADE_OUT = 0.22, 0.9
MAX_ITEMS = 40


def default_rect() -> list[int]:
    """預設放在主螢幕左側偏下，大約是遊戲聊天區上方的位置。"""
    left, top, width, height = oswin.work_area()
    w = max(MIN_SIZE[0], min(620, int(width * 0.30)))
    h = max(MIN_SIZE[1], int(height * 0.30))
    return [left + 24, top + int(height * 0.36), w, h]


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


def preset_rect(name: str) -> list[int]:
    place = PRESETS[name]["place"]
    if not place:
        return default_rect()
    left, top, width, height = oswin.work_area()
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

    def start(self, rect) -> bool:
        self.rect = list(rect)
        return True

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

    def rect_on_screen(rect) -> bool:
        """這個位置現在還在某個螢幕上嗎（拔掉第二個螢幕之後可能不在了）。"""
        x, y, w, h = rect
        r = wintypes.RECT(int(x), int(y), int(x + w), int(y + h))
        return bool(_u32.MonitorFromRect(ctypes.byref(r), 0))      # MONITOR_DEFAULTTONULL

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
                oswin.exclude_from_capture(hwnd)      # 讀螢幕的功能不要讀到自己顯示的字
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

    def rect_on_screen(rect) -> bool:
        return True


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
        self._tiles.clear()
        self._last = None
        self._wake.set()

    def _open(self) -> None:
        rect = self.service.cfg["overlay_rect"]
        if not (isinstance(rect, list) and len(rect) == 4 and rect_on_screen(rect)):
            rect = default_rect()
        self.rect = [int(v) for v in rect]
        window = self.window_class(on_rect=self._moved, on_done=lambda: self.set_edit(False))
        if not window.start(self.rect):
            self.service.log(f"floating chat window failed: {getattr(window, 'error', '')}")
            self.service.cfg.set(overlay=False)
            self.service.hub.publish("config", self.service.cfg.snapshot())
            self.service.notice("overlay_failed")
            return
        self.window = window
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
        return {"editing": self.editing, "running": self.running}

    # ---- 留言 ----

    def on_message(self, msg: dict) -> None:
        if msg.get("pending") or msg.get("error") or not msg.get("translated"):
            return
        kind = "me" if msg.get("who") == "me" else ("screen" if msg.get("via") == "screen" else "heard")
        if kind == "me" and not self.service.cfg["overlay_mine"]:
            return
        self.push(kind, msg["translated"], msg.get("original", ""), key=msg["id"])

    def push(self, kind: str, text: str, original: str = "", key=None) -> None:
        with self._lock:
            now = time.monotonic()
            for item in self.items:
                if key is not None and item["key"] == key:      # 同一則（例如按了「翻錯了」重翻）：換掉內容
                    item.update(text=text, original=original, at=now - FADE_IN, version=item["version"] + 1)
                    break
            else:
                self.items.append({"key": key if key is not None else f"x{now}", "kind": kind, "text": text,
                                   "original": original, "at": now, "version": 0})
                del self.items[:-MAX_ITEMS]
        self._wake.set()

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
        if not self.window:
            on = False
        if on == self.editing:
            return
        self.editing = on
        if self.window:
            self.window.set_edit(on)
        if not on and self.rect:
            ok = self.service.cfg.set(overlay_rect=list(self.rect))
            self.service.hub.publish("config", self.service.cfg.snapshot())
            self.service.stored("overlay_rect", ok)
        self.service.hub.publish("overlay", self.state())
        self._last = None
        self._wake.set()

    def reset_position(self) -> None:
        self.rect = default_rect()
        ok = self.service.cfg.set(overlay_rect=list(self.rect))
        self.service.hub.publish("config", self.service.cfg.snapshot())
        self.service.stored("overlay_rect", ok)
        self._last = None
        self._wake.set()

    def apply_preset(self, name: str) -> dict | None:
        """套用一個一鍵版面：外觀整組換掉、搬到那個版面的位置。回傳和 update_config 一樣；沒有這個版面回傳 None。"""
        if name not in PRESETS:
            return None
        self.rect = preset_rect(name)
        placed = self.service.cfg.set(overlay_rect=list(self.rect))
        result = self.service.update_config({**{k: DEFAULTS[k] for k in OVERLAY_LOOK}, **PRESETS[name]["look"]})
        self._last = None
        self._wake.set()
        return {**result, "saved": bool(result["saved"] and placed)}

    def _moved(self, rect, final: bool) -> None:
        self.rect = [int(v) for v in rect]
        if final:
            self.service.cfg.set(overlay_rect=list(self.rect))
        self._wake.set()

    # ---- 畫 ----

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
        key = (item["key"], item["version"], width, size, cfg["overlay_original"], cfg["overlay_backdrop"],
               cfg["ui_lang"], cfg["my_lang"], cfg["their_lang"], chatpaint.style_key(style))
        tile = self._tiles.get(key)
        if tile is None:
            names = chatpaint.NAMES.get(cfg["ui_lang"] or "en", chatpaint.NAMES["en"])
            original = item["original"] if cfg["overlay_original"] and item["original"] != item["text"] else ""
            tile = chatpaint.paint_message(item["kind"], names[item["kind"]], item["text"], width, size,
                                           original=original, backdrop=int(cfg["overlay_backdrop"]),
                                           han=chatpaint.han_kind(cfg["my_lang"], cfg["their_lang"]), style=style)
            if len(self._tiles) > 120:
                self._tiles.clear()
            self._tiles[key] = tile
        return tile

    def frame(self, now: float | None = None):
        """回傳 (圖, 下一次要重畫前可以等幾秒)。圖和上一次一樣時回傳 (None, 秒數)。"""
        now = time.monotonic() if now is None else now
        cfg = self.service.cfg.snapshot()
        window = self.window
        scale = getattr(window, "scale", 1.0) or 1.0
        size = max(12, round(int(cfg["overlay_font"]) * scale))
        life = 0.0 if self.editing else float(cfg["overlay_seconds"])
        width, height = max(40, self.rect[2]), max(40, self.rect[3])
        with self._lock:
            if life > 0:
                self.items = [i for i in self.items if now - i["at"] < life]
            items = list(self.items)
        if self.editing and not items:
            lang = cfg["ui_lang"] or "en"
            items = [{"key": f"sample{n}", "kind": kind, "text": text, "original": "", "at": now - 5, "version": 0}
                     for n, (kind, text) in enumerate(chatpaint.SAMPLES.get(lang, chatpaint.SAMPLES["en"]))]
        alphas = [1.0 if self.editing else self._alpha(i, now, life) for i in items]
        moving = any(a < 0.999 for a in alphas)
        wait = 0.04 if moving else 1.0
        if life > 0 and items and not moving:
            wait = max(0.04, min(1.0, min(life - FADE_OUT - (now - i["at"]) for i in items)))
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
        while not self._stop.is_set() and not self.service.closing.is_set():
            try:
                image, wait = self.frame()
                if image is not None and self.window is window:
                    window.paint(image, self.rect)
            except Exception as ex:  # noqa: BLE001
                self.service.log(f"floating chat paint failed: {ex!r}")
                wait = 1.0
            self._wake.wait(wait)
            self._wake.clear()
