"""系統匣圖示：關掉視窗之後 LiveLingo 還在這裡，點一下回到視窗，右鍵可以開關來源或結束。"""

import sys
import threading
import time

from .paths import resource_dir

IS_WIN = sys.platform == "win32"

TEXT = {
    "zh-TW": {"open": "開啟 LiveLingo", "screen": "讀螢幕", "listen": "聽對方說話", "mic": "聽我說話", "overlay": "浮動留言",
              "record": "記錄字幕", "quit": "結束 LiveLingo", "hint_title": "LiveLingo 還在執行",
              "hint": "視窗收到系統匣了。點一下圖示回到視窗，按右鍵可以結束。"},
    "zh-CN": {"open": "打开 LiveLingo", "screen": "读屏幕", "listen": "听对方说话", "mic": "听我说话", "overlay": "浮动留言",
              "record": "记录字幕", "quit": "退出 LiveLingo", "hint_title": "LiveLingo 还在运行",
              "hint": "窗口收到系统托盘了。点一下图标回到窗口，按右键可以退出。"},
    "en": {"open": "Open LiveLingo", "screen": "Read screen", "listen": "Hear them", "mic": "Hear me", "overlay": "Floating chat",
           "record": "Record subtitles", "quit": "Quit LiveLingo", "hint_title": "LiveLingo is still running",
           "hint": "The window went to the system tray. Click the icon to open it, right-click to quit."},
    "ko": {"open": "LiveLingo 열기", "screen": "화면 읽기", "listen": "상대방 말 듣기", "mic": "내 말 듣기", "overlay": "떠 있는 채팅",
           "record": "자막 기록", "quit": "LiveLingo 종료", "hint_title": "LiveLingo가 아직 실행 중입니다",
           "hint": "창을 시스템 트레이로 보냈습니다. 아이콘을 누르면 창으로 돌아가고, 오른쪽 버튼으로 종료할 수 있습니다."},
    "ja": {"open": "LiveLingo を開く", "screen": "画面を読む", "listen": "相手の声を聞く", "mic": "自分の声を聞く", "overlay": "フローティングチャット",
           "record": "字幕を記録", "quit": "LiveLingo を終了", "hint_title": "LiveLingo は実行中です",
           "hint": "ウィンドウをシステムトレイにしまいました。アイコンをクリックすると戻り、右クリックで終了できます。"},
}


def texts(ui_lang: str) -> dict:
    return TEXT.get(ui_lang) or TEXT["en"]


class Tray:
    """pystray 的包裝。圖示開不起來（沒有桌面、套件不相容）時 running 是 False，程式照常運作，只是關視窗就等於結束。"""

    def __init__(self, service):
        self.service = service
        self.icon = None
        self.running = False
        self.error = ""
        self._sig = None

    def _menu(self):
        import pystray

        s, t = self.service, (lambda key: (lambda item: texts(s.cfg["ui_lang"])[key]))
        source = lambda key: pystray.MenuItem(      # noqa: E731
            t(key), lambda icon, item: s.set_source(key, not s.sources[key]["on"]),
            checked=lambda item: bool(s.sources[key]["on"]), enabled=lambda item: not s.sources[key]["busy"])
        return pystray.Menu(
            pystray.MenuItem(t("open"), lambda icon, item: s.focus(), default=True),
            pystray.Menu.SEPARATOR,
            source("screen"), source("listen"), source("mic"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("overlay"), lambda icon, item: s.update_config({"overlay": not s.cfg["overlay"]}),
                             checked=lambda item: bool(s.cfg["overlay"])),
            pystray.MenuItem(t("record"), lambda icon, item: s.set_recording(not s.transcript.on),
                             checked=lambda item: bool(s.transcript.on), enabled=lambda item: not s.transcript.closing),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("quit"), lambda icon, item: s.on_quit()),
        )

    def start(self) -> bool:
        if not IS_WIN or self.icon is not None:
            return self.running
        try:
            import pystray
            from PIL import Image

            image = Image.open(resource_dir() / "data" / "tray.png")
            self.icon = pystray.Icon("LiveLingo", image, "LiveLingo", self._menu())
            self.icon.run_detached()
            self.running = True
            threading.Thread(target=self._watch, daemon=True, name="tray-watch").start()
        except Exception as ex:  # noqa: BLE001
            self.error = repr(ex)
            self.service.log(f"tray icon failed: {ex!r}")
            self.running = False
        return self.running

    def _signature(self):
        s = self.service
        return (s.cfg["ui_lang"], s.cfg["overlay"], s.transcript.on, s.transcript.closing,
                tuple((k, v["on"], v["busy"]) for k, v in sorted(s.sources.items())))

    def _watch(self) -> None:
        """來源、浮動留言、字幕記錄或介面語言變了，就把右鍵選單更新一次。"""
        while self.running and not self.service.closing.is_set():
            try:
                sig = self._signature()
                if sig != self._sig and self.icon is not None:
                    self._sig = sig
                    self.icon.update_menu()
            except Exception as ex:  # noqa: BLE001
                self.error = repr(ex)
            time.sleep(0.6)

    def notify_hidden(self) -> None:
        """第一次把視窗收到系統匣時提醒一次，之後不再提醒。"""
        if not self.running or self.service.cfg["tray_hint_shown"]:
            return
        self.service.cfg.set(tray_hint_shown=True)
        try:
            t = texts(self.service.cfg["ui_lang"])
            self.icon.notify(t["hint"], t["hint_title"])
        except Exception as ex:  # noqa: BLE001
            self.service.log(f"tray notification failed: {ex!r}")

    def stop(self) -> None:
        if self.icon is not None and self.running:
            self.running = False
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass

