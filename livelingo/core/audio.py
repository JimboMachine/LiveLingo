"""
收音：把聲音變成 16 kHz 單聲道的小段資料，放進佇列給語音辨識用。
  loopback = 電腦正在播放的聲音（喇叭、耳機裡聽到的，也就是對方說的話）
  mic      = 你的麥克風
只是「錄下電腦本來就在播的聲音」，不會碰 Discord 或遊戲的程式。
"""

import queue
import sys
import threading
import time
import warnings

import numpy as np

SR = 16000
BLOCK = 1600  # 每 0.1 秒一段


def _com_init() -> None:
    """Windows 的音訊介面要求每個執行緒自己先初始化 COM。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)


_sc_lock = threading.Lock()
_sc_done = threading.Event()
_sc_state: dict = {"started": False, "error": None}


def _import_soundcard():
    import soundcard  # noqa: F401


def _sc_loader():
    try:
        _import_soundcard()
    except Exception as ex:  # noqa: BLE001
        _sc_state["error"] = ex
    _sc_done.set()
    threading.Event().wait()   # 這條執行緒要一直留著，COM 才會保持在可用狀態


def _soundcard():
    """
    取得 soundcard 模組。
    soundcard 第一次載入時會自己初始化 COM，而且「這條執行緒之前已經初始化過」會被它當成錯誤
    （Error 0x100000001）。所以第一次載入一定放在一條全新的專用執行緒裡做，
    載入完成之後，其他執行緒才各自初始化 COM 來使用它。
    """
    with _sc_lock:
        if not _sc_state["started"]:
            _sc_state["started"] = True
            threading.Thread(target=_sc_loader, daemon=True, name="soundcard-loader").start()
    if not _sc_done.wait(60):
        raise RuntimeError("the audio component took too long to load")
    if _sc_state["error"] is not None:
        raise RuntimeError(f"the audio component failed to load: {_sc_state['error']!r}")
    _com_init()
    import soundcard

    return soundcard


def _pick(devices: list, wanted: str):
    """照名稱找裝置：先找完全一樣的，再找名稱裡包含這段字的。找不到回傳 None。"""
    for d in devices:
        if d.name == wanted or d.id == wanted:
            return d
    low = wanted.lower()
    for d in devices:
        if low in d.name.lower():
            return d
    return None


class Capture(threading.Thread):
    def __init__(self, kind: str, on_error=None, device: str | None = None, on_open=None):
        super().__init__(daemon=True)
        self.kind = kind                    # "loopback" 或 "mic"
        self.device = device                # 裝置名稱（或名稱的一部分）；None = 系統預設裝置
        self.on_error = on_error or (lambda kind, ex: None)
        self.on_open = on_open or (lambda kind, name: None)   # 成功開始收音時通知（帶裝置名稱）
        self.q: queue.Queue = queue.Queue(maxsize=600)   # 最多積 60 秒
        self.device_name = ""
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def _open(self):
        sc = _soundcard()

        if self.kind == "loopback":
            # 只在「播放裝置」裡找，才不會選到名字很像的麥克風
            devices = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
            wanted = self.device or sc.default_speaker().id
        else:
            devices = sc.all_microphones()
            wanted = self.device or sc.default_microphone().id
        dev = _pick(devices, wanted)
        if dev is None:
            raise RuntimeError(f"audio device not found: {wanted}")
        return dev

    def run(self):
        try:
            warnings.filterwarnings("ignore", message=".*data discontinuity.*")
            dev = self._open()
            self.device_name = getattr(dev, "name", "")
            with dev.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
                self.on_open(self.kind, self.device_name)
                while not self._halt.is_set():
                    data = rec.record(numframes=BLOCK)
                    block = np.ascontiguousarray(data[:, 0], dtype=np.float32)
                    try:
                        self.q.put_nowait(block)
                    except queue.Full:      # 辨識跟不上就丟掉最舊的
                        try:
                            self.q.get_nowait()
                        except queue.Empty:
                            pass
        except Exception as ex:  # noqa: BLE001
            if not self._halt.is_set():
                self.on_error(self.kind, ex)


class ArrayCapture(threading.Thread):
    """測試用：把一段現成的聲音當成「正在收到的聲音」餵進來。"""

    def __init__(self, kind: str, audio: np.ndarray, realtime: bool = True):
        super().__init__(daemon=True)
        self.kind = kind
        self.audio = audio.astype(np.float32)
        self.realtime = realtime
        self.q: queue.Queue = queue.Queue()
        self.device_name = "test"
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        for i in range(0, len(self.audio), BLOCK):
            if self._halt.is_set():
                return
            self.q.put(self.audio[i:i + BLOCK])
            if self.realtime:
                time.sleep(BLOCK / SR)
        while not self._halt.is_set():      # 播完之後持續送靜音
            self.q.put(np.zeros(BLOCK, dtype=np.float32))
            time.sleep(BLOCK / SR)


def list_devices() -> dict:
    """列出可用的播放裝置和麥克風（除錯用）。"""
    sc = _soundcard()
    return {"speakers": [s.name for s in sc.all_speakers()], "default_speaker": sc.default_speaker().name,
            "microphones": [m.name for m in sc.all_microphones()], "default_microphone": sc.default_microphone().name}
