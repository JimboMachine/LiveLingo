"""
程式的視窗：用 Windows 內建的 WebView2 把介面（網頁）包成一個一般的視窗。
萬一這台電腦沒有 WebView2，就改用 Edge 的「應用程式模式」開，功能一樣，只是不能自動調整視窗大小和置頂。
"""

import os
import shutil
import subprocess
import sys
import threading
import time

from . import oswin

MAIN_MIN = (380, 150)
SETTINGS_SIZE = (900, 800)
SUBTITLE_SIZE = (900, 150)
NORMAL_VIEWS = ("main", "settings")      # 這兩個畫面共用同一個視窗大小


class WindowCtl:
    """給程式主體用的視窗控制。"""

    native = True

    def __init__(self, win, service):
        self.win = win
        self.service = service
        self.main_geom = None       # 離開主畫面時記住的大小和位置
        self.subtitle_pos = None

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception as ex:  # noqa: BLE001
            self.service.log(f"window call failed: {ex!r}")
            return None

    def geometry(self):
        try:
            return int(self.win.width), int(self.win.height), int(self.win.x), int(self.win.y)
        except Exception:  # noqa: BLE001
            return None

    def set_on_top(self, on: bool) -> None:
        def work():
            self.win.on_top = bool(on)

        self._safe(work)

    def minimize(self) -> None:
        self._safe(self.win.minimize)

    def restore(self) -> None:
        self._safe(self.win.restore)

    def focus(self) -> None:
        self.restore()
        self._safe(self.win.show)
        if not self.service.cfg["on_top"] and self.service.view != "subtitle":
            self.set_on_top(True)       # 先置頂再取消，視窗才會真的跑到最前面
            self.set_on_top(False)

    def _screen(self):
        try:
            import webview

            s = webview.screens[0]
            return int(s.x), int(s.y), int(s.width), int(s.height)
        except Exception:  # noqa: BLE001
            return 0, 0, 1920, 1080

    def apply_view(self, view: str, previous: str) -> None:
        """對話和設定用同一個視窗大小，切換時不動；只有字幕列和第一次設定會改大小。"""
        geom = self.geometry()
        if previous in NORMAL_VIEWS and view not in NORMAL_VIEWS and geom:
            self.main_geom = geom
        if previous == "subtitle" and geom:
            self.subtitle_pos = (geom[2], geom[3])
        sx, sy, sw, sh = self._screen()
        if view == "subtitle":
            w, h = min(SUBTITLE_SIZE[0], sw - 80), SUBTITLE_SIZE[1]
            x, y = self.subtitle_pos or (sx + (sw - w) // 2, sy + sh - h - 110)
            self._safe(self.win.resize, w, h)
            self._safe(self.win.move, x, y)
            self.set_on_top(True)
            return
        if previous == "subtitle":
            self.set_on_top(self.service.cfg["on_top"])
        if view == "wizard":
            w, h = min(SETTINGS_SIZE[0], sw - 40), min(SETTINGS_SIZE[1], sh - 80)
            self._safe(self.win.resize, w, h)
            if geom:      # 變寬之後如果超出螢幕，往左移回來
                x = min(geom[2], sx + sw - w - 10)
                y = min(geom[3], sy + sh - h - 50)
                if (x, y) != (geom[2], geom[3]) or previous == "subtitle":
                    self._safe(self.win.move, max(sx, x), max(sy, y))
        elif previous not in NORMAL_VIEWS and self.main_geom:
            w, h, x, y = self.main_geom
            self._safe(self.win.resize, w, h)
            self._safe(self.win.move, x, y)

    def save_geometry(self) -> None:
        geom = self.geometry() if self.service.view in NORMAL_VIEWS else self.main_geom
        if geom and geom[0] >= MAIN_MIN[0] and geom[1] >= 300 and geom[2] > -30000:
            self.service.cfg.set(window={"w": geom[0], "h": geom[1], "x": geom[2], "y": geom[3]})

    def choose_folder(self, start: str = "") -> str:
        """跳出 Windows 的「選擇資料夾」視窗，回傳選到的資料夾；按取消回傳空字串。"""
        try:
            import webview

            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if kind is None:
                kind = webview.FOLDER_DIALOG
            picked = self.win.create_file_dialog(kind, directory=start or "")
            return str(picked[0]) if picked else ""
        except Exception as ex:  # noqa: BLE001
            self.service.log(f"folder dialog failed: {ex!r}")
            return ""

    def set_chrome(self, caption: str, text: str, dark: bool) -> bool:
        """標題列的顏色跟著介面的主題。"""
        try:
            return oswin.style_title_bar(int(self.win.native.Handle.ToInt64()), caption, text, dark)
        except Exception as ex:  # noqa: BLE001
            self.service.log(f"title bar colours failed: {ex!r}")
            return False


def _run_webview(url: str, service, on_loaded=None, quit_event: threading.Event | None = None) -> bool:
    """用 WebView2 開視窗；視窗關掉後回傳 True。開不起來回傳 False。"""
    quit_event = quit_event or threading.Event()
    if not oswin.has_webview2():
        service.log("WebView2 runtime was not found; using the browser instead")
        return False
    try:
        import webview
    except Exception as ex:  # noqa: BLE001
        service.log(f"pywebview is not available: {ex!r}")
        return False
    geom = service.cfg["window"]
    first = not service.cfg["setup_done"]
    width, height = (SETTINGS_SIZE if first else (int(geom["w"]), int(geom["h"])))
    kwargs = {}
    if not first and geom.get("x") is not None and geom.get("y") is not None:
        kwargs = {"x": int(geom["x"]), "y": int(geom["y"])}
    started = {"ok": False}
    try:
        win = webview.create_window("LiveLingo", url, width=width, height=height, min_size=MAIN_MIN,
                                    on_top=bool(service.cfg["on_top"]), background_color="#0F1113",
                                    text_select=True, **kwargs)
        ctl = WindowCtl(win, service)
        service.can_window = True
        if not first:
            ctl.main_geom = (int(geom["w"]), int(geom["h"]), geom.get("x") or 60, geom.get("y") or 60)

        def shown():
            started["ok"] = True
            service.window = ctl
            ctl.set_chrome(*service.chrome)
            try:
                oswin.exclude_from_capture(int(win.native.Handle.ToInt32()))
            except Exception as ex:  # noqa: BLE001
                service.log(f"exclude_from_capture failed: {ex!r}")

        def closing():
            ctl.save_geometry()
            # 關閉視窗時收到系統匣（可以在設定關掉）；真的要結束（系統匣的「結束」、更新、另一個地方要求離開）就照常關
            if service.cfg["close_to_tray"] and service.tray.running and not quit_event.is_set():
                try:
                    win.hide()
                except Exception as ex:  # noqa: BLE001
                    service.log(f"hide to tray failed: {ex!r}")
                    return None
                service.tray.notify_hidden()
                return False
            return None

        def watch():
            # 程式需要自己結束時（更新、另一個地方要求離開），把視窗關掉
            quit_event.wait()
            for w in list(webview.windows):
                try:
                    w.destroy()
                except Exception:  # noqa: BLE001
                    pass

        win.events.shown += shown
        win.events.closing += closing
        if on_loaded:
            win.events.loaded += lambda: on_loaded(win)
        storage = str(service.data / "webview")
        webview.start(watch, private_mode=False, storage_path=storage,
                      gui="edgechromium" if sys.platform == "win32" else None)
        return started["ok"]
    except Exception as ex:  # noqa: BLE001
        service.log(f"webview window failed: {ex!r}")
        service.can_window = service.window is not None
        return started["ok"]


def _find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(p):
                return p
    return shutil.which("msedge")


def _run_browser(url: str, service, quit_event: threading.Event) -> None:
    """備用方案：用 Edge 的應用程式模式（或預設瀏覽器）開介面，等介面關掉才結束。"""
    edge = _find_edge() if sys.platform == "win32" else None
    if edge:
        subprocess.Popen([edge, f"--app={url}", "--window-size=480,820",
                          f"--user-data-dir={service.data / 'edge-profile'}"], stdin=subprocess.DEVNULL)
    else:
        import webbrowser

        webbrowser.open(url)
    # 等介面連上來，之後介面全部關掉超過幾秒就結束程式
    deadline = time.time() + 120
    while not service.hub.ever_connected and time.time() < deadline and not quit_event.is_set():
        time.sleep(0.5)
    gone_since = None
    while not quit_event.is_set():
        time.sleep(1)
        if service.hub.count:
            gone_since = None
        else:
            gone_since = gone_since or time.time()
            if time.time() - gone_since > 6:
                return


def run(url: str, service, start=None) -> None:
    """顯示視窗，直到使用者把它關掉。start：視窗準備好接受「結束」的要求之後才呼叫（用來啟動背景工作）。"""
    quit_event = threading.Event()
    service.on_quit = quit_event.set
    if start:
        start()
    if os.environ.get("LIVELINGO_NO_WINDOW"):       # 測試用：不開視窗，等到被要求結束
        quit_event.wait()
        return
    if not _run_webview(url, service, None, quit_event) and not quit_event.is_set():
        _run_browser(url, service, quit_event)
