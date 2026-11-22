"""
全域快速鍵：打遊戲的時候不用切回 LiveLingo，按一組鍵就能開關字幕、開關麥克風、複製上一句翻譯、叫出快速輸入框。

用的是 Windows 的 RegisterHotKey：只是請 Windows「這組鍵被按下時通知我」，不會去攔截或讀取其他按鍵。
同一組鍵一次只能給一個程式用，被別的程式先登記走的會標成 taken，讓使用者換一組。
"""

import sys
import threading

IS_WIN = sys.platform == "win32"

ACTIONS = ("overlay", "mic", "copy", "quick")      # 開關字幕、開關麥克風、複製上一句翻譯、快速輸入框
MODS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4, "Win": 0x8}      # 也是顯示的順序
_NOREPEAT = 0x4000                                  # 按住不放不要一直重複觸發
KEYS = {**{chr(c): c for c in range(ord("A"), ord("Z") + 1)}, **{str(d): ord(str(d)) for d in range(10)},
        **{f"F{n}": 0x6F + n for n in range(1, 13)}}

# 按了快速鍵之後顯示在浮動留言上的短提示（人在遊戲裡，看不到主視窗）
NOTES = {
    "zh-TW": {"mic_on": "麥克風：開", "mic_off": "麥克風：關", "copied": "已複製上一句翻譯", "nothing": "還沒有可以複製的翻譯",
              "not_ready": "翻譯引擎還沒準備好",
              "skipped": "聊天太快，有 {n} 則來不及翻，已略過",
              "engine_restarting": "翻譯引擎停了，正在自動重開…",
              "engine_back": "翻譯引擎回來了，剛剛沒翻的句子現在補上"},
    "zh-CN": {"mic_on": "麦克风：开", "mic_off": "麦克风：关", "copied": "已复制上一句翻译", "nothing": "还没有可以复制的翻译",
              "not_ready": "翻译引擎还没准备好",
              "skipped": "聊天太快，有 {n} 条来不及翻，已略过",
              "engine_restarting": "翻译引擎停了，正在自动重启…",
              "engine_back": "翻译引擎恢复了，刚才没翻的句子现在补上"},
    "en": {"mic_on": "Microphone on", "mic_off": "Microphone off", "copied": "Last translation copied",
           "nothing": "Nothing to copy yet", "not_ready": "The translation engine is not ready yet",
           "skipped": "Chat is moving too fast: {n} lines were skipped",
           "engine_restarting": "The translation engine stopped. Restarting it…",
           "engine_back": "The translation engine is back. Waiting sentences are being translated"},
    "ko": {"mic_on": "마이크 켜짐", "mic_off": "마이크 꺼짐", "copied": "마지막 번역을 복사했습니다", "nothing": "아직 복사할 번역이 없습니다",
           "not_ready": "번역 엔진이 아직 준비되지 않았습니다",
           "skipped": "채팅이 너무 빨라서 {n}줄을 건너뛰었습니다",
           "engine_restarting": "번역 엔진이 멈춰서 다시 시작하는 중입니다…",
           "engine_back": "번역 엔진이 다시 켜졌습니다. 밀린 문장을 이어서 번역합니다"},
    "ja": {"mic_on": "マイク：オン", "mic_off": "マイク：オフ", "copied": "直前の翻訳をコピーしました", "nothing": "コピーできる翻訳がまだありません",
           "not_ready": "翻訳エンジンの準備がまだできていません",
           "skipped": "チャットの流れが速いため、{n} 件を飛ばしました",
           "engine_restarting": "翻訳エンジンが止まったため、再起動しています…",
           "engine_back": "翻訳エンジンが復帰しました。待っていた文をこれから翻訳します"},
}


def parse(combo: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+S" → (修飾鍵, 按鍵碼)。一定要有 Ctrl、Alt、Shift 或 Win 其中一個，不然會搶走平常打字和遊戲用的鍵。"""
    parts = [p.strip() for p in str(combo or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    names = {m.lower(): m for m in MODS}
    mods = 0
    for part in parts[:-1]:
        name = names.get(part.lower())
        if name is None or mods & MODS[name]:
            return None
        mods |= MODS[name]
    key = parts[-1].upper()
    return (mods, KEYS[key]) if key in KEYS else None


def normalize(combo: str) -> str:
    """整理成固定的寫法（"alt + ctrl+s" → "Ctrl+Alt+S"）；不能用的回傳空字串。"""
    parsed = parse(combo)
    if not parsed:
        return ""
    mods, vk = parsed
    key = next(name for name, code in KEYS.items() if code == vk)
    return "+".join([name for name, bit in MODS.items() if mods & bit] + [key])


class Hotkeys:
    """
    set(bindings) 之後照著登記；按下時呼叫 on_fire(動作名稱)。states 是每個動作現在的狀態：
    ok（可以用）/ taken（被別的程式佔用）/ off（沒設定）。register / unregister 測試時可以換成假的。
    """

    def __init__(self, on_fire, on_state=None, backend=None):
        self.on_fire = on_fire
        self.on_state = on_state or (lambda states: None)
        self.backend = backend if backend is not None else (_WinBackend() if IS_WIN else _NoBackend())
        self.bindings: dict[str, str] = {}
        self.states: dict[str, str] = {name: "off" for name in ACTIONS}
        self._lock = threading.Lock()
        self._paused = False

    @property
    def available(self) -> bool:
        return self.backend.available

    def start(self) -> None:
        self.backend.start(self._fired)

    def stop(self) -> None:
        self.backend.stop()

    def set(self, bindings: dict) -> dict:
        """換成這一組設定。回傳每個動作的狀態。"""
        wanted = {name: normalize(bindings.get(name, "")) for name in ACTIONS}
        with self._lock:
            self.bindings = wanted
            self._paused = False
            states = self._apply()
        self.on_state(states)
        return states

    def _apply(self) -> dict:
        wanted = self.bindings
        results = self.backend.apply({n: parse(c) for n, c in wanted.items() if c}) if self.available else {}
        self.states = {name: ("off" if not wanted[name] or not self.available else ("ok" if results.get(name) else "taken"))
                       for name in ACTIONS}
        return dict(self.states)

    def pause(self, on: bool) -> None:
        """設定頁正在等使用者按新的組合：先把登記的都放掉，不然按到現在那一組會直接觸發動作。on=False 再登記回來。"""
        with self._lock:
            if on == self._paused:
                return
            self._paused = on
            if on:
                if self.available:
                    self.backend.apply({})
                return
            states = self._apply()
        self.on_state(states)

    @property
    def paused(self) -> bool:
        return self._paused

    def _fired(self, name: str) -> None:
        if name in ACTIONS:
            self.on_fire(name)


class _NoBackend:
    """不是 Windows：沒有全域快速鍵。設定照樣存，狀態一律是 off。"""

    available = False

    def start(self, on_fire) -> None:
        pass

    def stop(self) -> None:
        pass

    def apply(self, wanted: dict) -> dict:
        return {}


class FakeBackend:
    """測試和開發用：假裝登記成功；taken 裡的組合（例如 "Ctrl+Alt+C"）假裝被別的程式佔用。press(組合) 模擬按下。"""

    available = True

    def __init__(self, taken=()):
        self.taken = {parse(c) for c in taken}
        self.registered: dict = {}
        self._on_fire = lambda name: None

    def start(self, on_fire) -> None:
        self._on_fire = on_fire

    def stop(self) -> None:
        self.registered = {}

    def apply(self, wanted: dict) -> dict:
        results = {name: combo not in self.taken for name, combo in wanted.items()}
        self.registered = {combo: name for name, combo in wanted.items() if results[name]}
        return results

    def press(self, combo: str) -> bool:
        name = self.registered.get(parse(combo))
        if name:
            self._on_fire(name)
        return bool(name)


class _WinBackend:
    """一條專門的執行緒：登記快速鍵、等 Windows 通知。登記和取消都要在同一條執行緒上做。"""

    available = True
    _WM_HOTKEY, _WM_APPLY, _WM_QUIT = 0x0312, 0x8001, 0x0012

    def __init__(self):
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._done = threading.Event()
        self._wanted: dict = {}
        self._results: dict = {}
        self._ids: dict[int, str] = {}
        self._on_fire = lambda name: None

    def start(self, on_fire) -> None:
        if self._thread:
            return
        self._on_fire = on_fire
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")
        self._thread.start()
        self._ready.wait(3)

    def stop(self) -> None:
        if self._thread and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self._WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None

    def apply(self, wanted: dict) -> dict:
        if not self._thread_id:
            return {}
        import ctypes

        self._wanted = dict(wanted)
        self._done.clear()
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, self._WM_APPLY, 0, 0)
        self._done.wait(3)
        return dict(self._results)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        msg = wintypes.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)      # 先碰一下，這條執行緒才會有訊息佇列
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._ready.set()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self._WM_HOTKEY:
                name = self._ids.get(int(msg.wParam))
                if name:
                    try:
                        self._on_fire(name)
                    except Exception:  # noqa: BLE001  （動作出錯不能讓這條執行緒停掉）
                        pass
            elif msg.message == self._WM_APPLY:
                for number in list(self._ids):
                    u32.UnregisterHotKey(None, number)
                self._ids, results = {}, {}
                for number, (name, (mods, vk)) in enumerate(self._wanted.items(), start=1):
                    ok = bool(u32.RegisterHotKey(None, number, mods | _NOREPEAT, vk))
                    results[name] = ok
                    if ok:
                        self._ids[number] = name
                self._results = results
                self._done.set()
        for number in list(self._ids):
            u32.UnregisterHotKey(None, number)
        self._ids = {}
        self._thread_id = 0
