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


log = lambda text: None      # noqa: E731  程式主體會換成真的紀錄函式


def describe(ex: BaseException) -> str:
    """把錯誤變成看得懂的一行字。有些錯誤（例如 AssertionError）本身沒有訊息，就補上是哪一行出的錯，
    不然介面上只會看到開關自己跳回去，完全不知道為什麼。"""
    text = f"{type(ex).__name__}: {ex}" if str(ex) else type(ex).__name__
    if not str(ex) and ex.__traceback__ is not None:
        import traceback

        frame = traceback.extract_tb(ex.__traceback__)[-1]
        text += f" ({frame.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{frame.lineno} {frame.line or ''})".rstrip()
    return text


def portaudio_input(sd, wanted: str | None):
    """在第二種收音方式（PortAudio）裡找麥克風：回傳 (裝置編號, 名稱)。
    用 Windows 最舊、相容性最好的 MME 介面，它的裝置名稱最多 31 個字，所以用「開頭一樣」來比對。
    找不到指定的那支就用預設的麥克風。"""
    devices = sd.query_devices()
    apis = sd.query_hostapis()
    order = sorted(range(len(apis)), key=lambda i: {"MME": 0, "Windows DirectSound": 1}.get(apis[i]["name"], 2))
    for i in order:
        api = apis[i]
        if api["name"] == "Windows WDM-KS":      # 這個介面常常要獨占裝置，不用
            continue
        inputs = [d for d in api["devices"] if devices[d]["max_input_channels"] > 0]
        if wanted:
            low = wanted.lower()
            for d in inputs:
                name = devices[d]["name"].lower()
                if name and (name == low or low.startswith(name) or name.startswith(low)):
                    return d, devices[d]["name"]
        default = api.get("default_input_device", -1)
        if default in inputs:
            return default, devices[default]["name"]
        if inputs:
            return inputs[0], devices[inputs[0]]["name"]
    raise RuntimeError("no microphone found")


class Capture(threading.Thread):
    def __init__(self, kind: str, on_error=None, device: str | None = None, on_open=None):
        super().__init__(daemon=True)
        self.kind = kind                    # "loopback" 或 "mic"
        self.device = device                # 裝置名稱（或名稱的一部分）；None = 系統預設裝置
        self.on_error = on_error or (lambda kind, ex: None)
        self.on_open = on_open or (lambda kind, name: None)   # 成功開始收音時通知（帶裝置名稱）
        self.q: queue.Queue = queue.Queue(maxsize=600)   # 最多積 60 秒
        self.device_name = ""
        self.backend = ""                   # 實際用哪一種方式收到聲音：soundcard 或 portaudio
        self.delivered = False              # 有沒有收到過聲音資料
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
        if dev is None and self.device:
            # 之前選的裝置現在不見了（拔掉、換了插孔）：改用 Windows 預設的，不要整個開不起來
            fallback = sc.default_speaker().id if self.kind == "loopback" else sc.default_microphone().id
            dev = _pick(devices, fallback)
        if dev is None:
            raise RuntimeError(f"audio device not found: {wanted}")
        return dev

    def _push(self, block: np.ndarray) -> None:
        self.delivered = True
        try:
            self.q.put_nowait(block)
        except queue.Full:      # 辨識跟不上就丟掉最舊的
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass

    def _run_soundcard(self) -> None:
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        dev = self._open()
        self.device_name = getattr(dev, "name", "")
        with dev.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
            self.backend = "soundcard"
            self.on_open(self.kind, self.device_name)
            while not self._halt.is_set():
                data = rec.record(numframes=BLOCK)
                self._push(np.ascontiguousarray(data[:, 0], dtype=np.float32))

    def _run_portaudio(self) -> None:
        """第二種收音方式。只收得到麥克風（收不到電腦播放的聲音），當第一種打不開麥克風時的備援。"""
        import sounddevice as sd

        index, name = portaudio_input(sd, self.device_name or None)

        def callback(indata, frames, when, status):  # noqa: ARG001
            self._push(np.ascontiguousarray(indata[:, 0], dtype=np.float32))

        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK, device=index,
                            callback=callback):
            self.device_name = name
            self.backend = "portaudio"
            self.on_open(self.kind, name)
            while not self._halt.wait(0.2):
                pass

    def run(self):
        try:
            self._run_soundcard()
            return
        except Exception as ex:  # noqa: BLE001
            if self._halt.is_set():
                return
            first = describe(ex)
            log(f"{self.kind} capture ({self.device or 'default'}) failed: {first}")
            if self.kind != "mic" or self.delivered:      # 收到一半才斷掉（裝置被拔掉）就不換方式重來
                self.on_error(self.kind, RuntimeError(first))
                return
        try:
            self._run_portaudio()
        except Exception as ex:  # noqa: BLE001
            if not self._halt.is_set():
                second = describe(ex)
                log(f"mic capture, second way, failed too: {second}")
                self.on_error(self.kind, RuntimeError(f"{first} / {second}"))


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
