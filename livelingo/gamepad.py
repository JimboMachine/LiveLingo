"""手把快捷鍵（2.17.0）：按住 Back（View）再按另一顆鍵，就做跟鍵盤快速鍵一樣的事（回話建議、框選翻譯……），手不用離開手把。
只讀手把按鈕的狀態（Windows 的 XInput），不會替手把按任何鍵；沒開這個功能就不會去讀。"""

import sys
import threading
import time

IS_WIN = sys.platform == "win32"

# XInput 的按鈕位元（也是顯示的名字）。Back 在 Xbox 手把上叫 View、Start 叫 Menu
BUTTONS = {"Up": 0x0001, "Down": 0x0002, "Left": 0x0004, "Right": 0x0008, "Start": 0x0010, "Back": 0x0020,
           "LS": 0x0040, "RS": 0x0080, "LB": 0x0100, "RB": 0x0200, "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000}
# 一定要按著其中一組才算（不然會跟遊戲本身的按鍵撞）：Back，或 LB＋RB 一起
HOLDS = (("Back",), ("LB", "RB"))
DEFAULTS = {"reply": "Back+A", "mic": "Back+X", "overlay": "Back+B", "snap": "Back+Y", "keys": "Back+Start"}
POLL = 0.05


def normalize(combo: str) -> str:
    """「back + a」→「Back+A」；不是合法的組合（沒有 Back 或 LB＋RB、只有一顆、不認得的鍵）回傳空字串。"""
    names = {k.lower(): k for k in BUTTONS}
    parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
    if not 2 <= len(parts) <= 3 or len(set(parts)) != len(parts) or any(p not in names for p in parts):
        return ""
    got = [names[p] for p in parts]
    if not any(all(h in got for h in hold) for hold in HOLDS):
        return ""
    order = list(BUTTONS)
    hold = next(h for h in HOLDS if all(x in got for x in h))
    rest = sorted((g for g in got if g not in hold), key=order.index)
    if not rest:
        return ""
    return "+".join([*hold, *rest])


def mask_of(combo: str) -> int:
    combo = normalize(combo)
    return sum(BUTTONS[p] for p in combo.split("+")) if combo else 0


def clean(keys, actions) -> dict:
    """{動作: 組合}：只留認得的動作和合法的組合，兩個動作不能用同一組。"""
    out: dict[str, str] = {}
    if not isinstance(keys, dict):
        return out
    for name, combo in keys.items():
        fixed = normalize(combo)
        if name in actions and fixed and fixed not in out.values():
            out[name] = fixed
    return out


class Matcher:
    """按鈕狀態一直送進來，組合「剛好按下去」的那一刻回傳動作名字（按著不放不會一直觸發）。"""

    def __init__(self, keys: dict):
        self.keys = {name: mask_of(c) for name, c in keys.items() if mask_of(c)}
        self.before = 0

    def feed(self, buttons: int) -> str:
        before, self.before = self.before, buttons
        hits = [(bin(m).count("1"), name) for name, m in self.keys.items() if buttons & m == m and before & m != m]
        return max(hits)[1] if hits else ""


def _xinput():
    import ctypes
    from ctypes import wintypes

    class GAMEPAD(ctypes.Structure):
        _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                    ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]

    class STATE(ctypes.Structure):
        _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", GAMEPAD)]

    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = getattr(ctypes.windll, name)
            break
        except OSError:
            continue
    else:
        return None
    state = STATE()

    def read() -> int:
        buttons = 0
        for user in range(4):
            if dll.XInputGetState(user, ctypes.byref(state)) == 0:
                buttons |= state.Gamepad.wButtons
        return buttons

    return read


def reader():
    """回傳一個讀「所有接著的手把現在按著哪些鍵」的函式；這台電腦沒有 XInput 就是 None。"""
    if not IS_WIN:
        return None
    try:
        return _xinput()
    except Exception:  # noqa: BLE001
        return None


class Gamepad:
    """在背景一直看手把；組合按下去就呼叫 on_action(動作名字)。read 是測試時可以換掉的讀取函式。"""

    def __init__(self, on_action, read=None, interval: float = POLL):
        self.on_action = on_action
        self.read = read
        self.interval = interval
        self.matcher = Matcher({})
        self.available = False
        self._stop = threading.Event()
        self._thread = None

    def set(self, keys: dict) -> None:
        self.matcher = Matcher(keys)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self.available
        self.read = self.read or reader()
        self.available = self.read is not None
        if not self.available:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gamepad")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(1)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                name = self.matcher.feed(int(self.read() or 0))
                if name:
                    self.on_action(name)
            except Exception:  # noqa: BLE001
                time.sleep(1.0)
            self._stop.wait(self.interval)
