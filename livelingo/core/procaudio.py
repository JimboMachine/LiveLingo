"""
只收指定程式的聲音（例如只聽 Discord，不聽遊戲音效和音樂）。

用的是 Windows 內建的功能（Process Loopback，Windows 10 2004 之後和 Windows 11 都有）：
請 Windows 把「這個程式和它底下的子程序正在播的聲音」另外給我們一份。
不會碰那個程式本身，也不用裝虛擬音效卡。可以同時指定好幾個程式，聲音會混在一起。

AppCapture 對外的樣子跟 audio.Capture 一樣（.q 裡是 16 kHz 單聲道的小段聲音），語音辨識那邊不用改。
"""

import ctypes
import queue
import sys
import threading
import time
import uuid

import numpy as np

from .audio import BLOCK, SR, describe

IS_WIN = sys.platform == "win32"
DEVICE = "VAD\\Process_Loopback"      # Windows 給「單一程式回放擷取」用的虛擬裝置名稱

_S_OK = 0
_E_NOINTERFACE = 0x80004002
_SILENT = 0x2                          # AUDCLNT_BUFFERFLAGS_SILENT
_LOOPBACK, _EVENT, _AUTOCONVERT, _SRC_QUALITY = 0x00020000, 0x00040000, 0x80000000, 0x08000000


def available() -> bool:
    """這台電腦的 Windows 有沒有這個功能（Windows 10 2004，組建 19041 以上）。"""
    return IS_WIN and sys.getwindowsversion().build >= 19041


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str = ""):
        super().__init__()
        if text:
            ctypes.memmove(ctypes.byref(self), uuid.UUID(text).bytes_le, 16)

    def same(self, other: "GUID") -> bool:
        return bytes(self) == bytes(other)


IID_IUNKNOWN = GUID("00000000-0000-0000-C000-000000000046")
IID_AGILE = GUID("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_HANDLER = GUID("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_AUDIO_CLIENT = GUID("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_CAPTURE_CLIENT = GUID("C8ADBD64-E71E-48A0-A4DE-185C395CD317")


class ActivationParams(ctypes.Structure):      # AUDIOCLIENT_ACTIVATION_PARAMS
    _fields_ = [("type", ctypes.c_int32),      # 1 = 單一程式回放
                ("pid", ctypes.c_uint32),
                ("mode", ctypes.c_int32)]      # 0 = 這個程序和它底下的子程序


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class PropVariant(ctypes.Structure):           # 只用得到 VT_BLOB 這一種
    _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16), ("r2", ctypes.c_uint16), ("r3", ctypes.c_uint16),
                ("blob", _Blob)]


class WaveFormat(ctypes.Structure):            # WAVEFORMATEX
    _pack_ = 1
    _fields_ = [("tag", ctypes.c_uint16), ("channels", ctypes.c_uint16), ("rate", ctypes.c_uint32),
                ("bytes_per_sec", ctypes.c_uint32), ("block_align", ctypes.c_uint16), ("bits", ctypes.c_uint16),
                ("extra", ctypes.c_uint16)]


def pcm_format(rate: int, channels: int = 2) -> WaveFormat:
    return WaveFormat(1, channels, rate, rate * channels * 2, channels * 2, 16, 0)


def to_mono(raw: bytes, channels: int) -> np.ndarray:
    """16 位元的聲音資料 → 單聲道、-1 到 1 的小數。"""
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return data.reshape(-1, channels).mean(axis=1) if channels > 1 else data


class Resampler:
    """把任意取樣率的單聲道聲音變成 16 kHz：先濾掉 7 kHz 以上（不然會有雜音），再照比例取樣。一段一段餵也接得起來。"""

    def __init__(self, rate: int, taps: int = 63):
        self.step = rate / SR
        n = np.arange(taps) - (taps - 1) / 2
        cutoff = 7000 / rate
        kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        self.kernel = (kernel / kernel.sum()).astype(np.float32)
        self.tail = np.zeros(taps - 1, dtype=np.float32)      # 上一段的尾巴，濾波要用
        self.last = 0.0                                       # 上一段濾好的最後一個值（跨段內插用）
        self.pos = 0.0                                        # 下一個輸出點落在這一段的哪個位置

    def feed(self, x: np.ndarray) -> np.ndarray:
        if not len(x):
            return np.zeros(0, dtype=np.float32)
        joined = np.concatenate([self.tail, x.astype(np.float32)])
        y = np.convolve(joined, self.kernel, mode="valid")    # 長度剛好等於 len(x)
        self.tail = joined[-(len(self.kernel) - 1):]
        # 只出到這一段最後一個值為止；落在它和下一段第一個值中間的點留到下一段再算（那時位置是負的，用 last 接）
        points = np.arange(self.pos, len(y) - 1 + 1e-9, self.step)
        out = np.interp(points, np.arange(-1, len(y)), np.concatenate([[self.last], y])).astype(np.float32)
        self.pos = (points[-1] + self.step if len(points) else self.pos) - len(y)
        self.last = float(y[-1])
        return out


def _call(ptr, index: int, *argtypes):
    """COM 物件的第 index 個方法（照介面定義的順序）。回傳一個可以直接呼叫的函式，結果是 HRESULT。"""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])
    return lambda *args: fn(ptr, *args)


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def _release(ptr) -> None:
    if ptr:
        try:
            _call(ptr, 2)()
        except Exception:  # noqa: BLE001
            pass


class _Handler:
    """Windows 啟用好收音介面之後會回頭呼叫的小物件（IActivateAudioInterfaceCompletionHandler）。"""

    def __init__(self):
        self.done = threading.Event()
        qi_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        ref_t = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        done_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)

        def query(this, iid, out):
            # IAgileObject 一定要認：Windows 會從別的執行緒回來呼叫，不認的話它會直接拒絕
            if iid and any(iid.contents.same(known) for known in (IID_IUNKNOWN, IID_HANDLER, IID_AGILE)):
                out[0] = this
                return _S_OK
            out[0] = None
            return ctypes.c_long(_E_NOINTERFACE).value

        def completed(_this, _operation):
            self.done.set()
            return _S_OK

        # 這些函式和表格都要留著，不然 Python 回收掉之後 Windows 回頭呼叫就當掉
        self._funcs = (qi_t(query), ref_t(lambda _this: 2), ref_t(lambda _this: 1), done_t(completed))
        self._vtable = (ctypes.c_void_p * 4)(*(ctypes.cast(f, ctypes.c_void_p) for f in self._funcs))
        self._object = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.addressof(self._object)


_KEEP: list = []


def activate(pid: int, timeout: float = 5.0) -> int:
    """請 Windows 準備「只收這個程序（和它的子程序）」的收音介面，回傳 IAudioClient 的指標。"""
    mmdev = ctypes.WinDLL("Mmdevapi.dll")
    start = mmdev.ActivateAudioInterfaceAsync
    start.restype = ctypes.c_long
    start.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID), ctypes.POINTER(PropVariant), ctypes.c_void_p,
                      ctypes.POINTER(ctypes.c_void_p)]
    params = ActivationParams(1, pid, 0)
    variant = PropVariant()
    variant.vt = 65                                           # VT_BLOB
    variant.blob.size = ctypes.sizeof(params)
    variant.blob.data = ctypes.addressof(params)
    handler = _Handler()
    _KEEP.append(handler)                                     # Windows 之後可能還會碰它，留著比較安全（很小）
    del _KEEP[:-32]
    operation = ctypes.c_void_p()
    _check(start(DEVICE, ctypes.byref(IID_AUDIO_CLIENT), ctypes.byref(variant), handler.pointer,
                 ctypes.byref(operation)), "ActivateAudioInterfaceAsync")
    try:
        if not handler.done.wait(timeout):
            raise OSError("Windows did not answer the request for this program's sound in time")
        result, unknown = ctypes.c_long(0), ctypes.c_void_p()
        # IActivateAudioInterfaceAsyncOperation::GetActivateResult
        _check(_call(operation, 3, ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(result), ctypes.byref(unknown)), "GetActivateResult")
        _check(result.value, "activating the program's sound")
        try:
            client = ctypes.c_void_p()
            _check(_call(unknown, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_AUDIO_CLIENT), ctypes.byref(client)), "QueryInterface(IAudioClient)")
            return client.value
        finally:
            _release(unknown)
    finally:
        _release(operation)


class ProcessStream(threading.Thread):
    """一個程序（連同子程序）的聲音。收到的聲音轉成 16 kHz 單聲道，交給 on_audio(這個串流, 聲音)。"""

    def __init__(self, pid: int, on_audio, on_end=None):
        super().__init__(daemon=True, name=f"app-audio-{pid}")
        self.pid = pid
        self.on_audio = on_audio
        self.on_end = on_end or (lambda stream, error: None)
        self.opened = threading.Event()
        self.error: Exception | None = None
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def _initialize(self, client: int) -> WaveFormat:
        """照順序試幾種格式和選項，第一個 Windows 接受的就用。"""
        init = _call(client, 3, ctypes.c_int, ctypes.c_uint32, ctypes.c_int64, ctypes.c_int64,
                     ctypes.POINTER(WaveFormat), ctypes.c_void_p)
        last = 0
        for rate in (48000, 44100):
            for flags in (_LOOPBACK | _EVENT, _LOOPBACK | _EVENT | _AUTOCONVERT | _SRC_QUALITY):
                fmt = pcm_format(rate)
                last = init(0, flags, 2_000_000, 0, ctypes.byref(fmt), None)      # 緩衝 0.2 秒
                if last >= 0:
                    return fmt
        _check(last, "IAudioClient::Initialize")
        raise OSError("IAudioClient::Initialize failed")

    def run(self) -> None:
        client = capture = event = None
        k32 = ctypes.windll.kernel32
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)       # 這條執行緒要自己初始化 COM
            client = activate(self.pid)
            fmt = self._initialize(client)
            k32.CreateEventW.restype = ctypes.c_void_p
            event = k32.CreateEventW(None, False, False, None)
            _check(_call(client, 13, ctypes.c_void_p)(event), "SetEventHandle")
            service = ctypes.c_void_p()
            _check(_call(client, 14, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_CAPTURE_CLIENT), ctypes.byref(service)), "GetService(IAudioCaptureClient)")
            capture = service.value
            get_buffer = _call(capture, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                               ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p)
            release_buffer = _call(capture, 4, ctypes.c_uint32)
            next_size = _call(capture, 5, ctypes.POINTER(ctypes.c_uint32))
            _check(_call(client, 10)(), "IAudioClient::Start")
            resampler = Resampler(fmt.rate)
            frame_bytes = fmt.block_align
            self.opened.set()
            while not self._halt.is_set():
                k32.WaitForSingleObject(ctypes.c_void_p(event), 200)      # 程式沒在播聲音時不會有通知，等一下再看
                while not self._halt.is_set():
                    frames = ctypes.c_uint32(0)
                    if next_size(ctypes.byref(frames)) < 0 or not frames.value:
                        break
                    data, flags = ctypes.c_void_p(), ctypes.c_uint32(0)
                    _check(get_buffer(ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags), None, None),
                           "IAudioCaptureClient::GetBuffer")
                    try:
                        if frames.value:
                            if flags.value & _SILENT or not data.value:
                                mono = np.zeros(frames.value, dtype=np.float32)
                            else:
                                mono = to_mono(ctypes.string_at(data.value, frames.value * frame_bytes), fmt.channels)
                            self.on_audio(self, resampler.feed(mono))
                    finally:
                        release_buffer(frames.value)
        except Exception as ex:  # noqa: BLE001
            self.error = ex
        finally:
            self.opened.set()
            try:
                if client:
                    _call(client, 11)()                       # Stop
            except Exception:  # noqa: BLE001
                pass
            _release(capture)
            _release(client)
            if event:
                k32.CloseHandle(ctypes.c_void_p(event))
            self.on_end(self, self.error)


class AppCapture(threading.Thread):
    """
    好幾個程式的聲音混成一路。exes 是程式檔名（Discord.exe…）；程式關掉再開、晚一點才開都沒關係，
    每隔幾秒會重新找一次。find(exes) 回傳 {檔名: [最上層的程序編號]}；make_stream 測試時可以換成假的。
    """

    backend = "process"

    def __init__(self, exes: list[str], find, on_error=None, on_open=None, make_stream=None, rescan: float = 3.0):
        super().__init__(daemon=True, name="app-audio")
        self.kind = "loopback"
        self.exes = list(exes)
        self.find = find
        self.on_error = on_error or (lambda kind, ex: None)
        self.on_open = on_open or (lambda kind, name: None)
        self.make_stream = make_stream or (lambda pid, on_audio, on_end: ProcessStream(pid, on_audio, on_end))
        self.rescan = rescan
        self.q: queue.Queue = queue.Queue(maxsize=600)
        self.device_name = ""
        self.delivered = False
        self.streams: dict[int, object] = {}                 # 程序編號 → 串流
        self.failed: dict[int, str] = {}                     # 開不起來的程序編號 → 原因
        self._buffers: dict[int, np.ndarray] = {}
        self._lock = threading.Lock()
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def _on_audio(self, stream, samples: np.ndarray) -> None:
        with self._lock:
            if stream.pid in self._buffers:
                joined = np.concatenate([self._buffers[stream.pid], samples])
                self._buffers[stream.pid] = joined[-SR:]      # 最多積一秒，再多就丟掉舊的

    def _on_end(self, stream, error) -> None:
        with self._lock:
            self._buffers.pop(stream.pid, None)
            if self.streams.get(stream.pid) is stream:
                del self.streams[stream.pid]
            if error is not None:
                self.failed[stream.pid] = describe(error)

    def _scan(self) -> None:
        """看看要聽的程式現在有哪些程序：新出現的開始收，不見的停掉。"""
        found = self.find(self.exes)
        wanted = {pid for pids in found.values() for pid in pids}
        with self._lock:
            current = dict(self.streams)
        for pid, stream in current.items():
            if pid not in wanted:
                stream.stop()
        for pid in sorted(wanted - set(current)):
            if pid in self.failed:
                continue
            stream = self.make_stream(pid, self._on_audio, self._on_end)
            with self._lock:
                self.streams[pid] = stream
                self._buffers[pid] = np.zeros(0, dtype=np.float32)
            stream.start()
        names = [exe[:-4] if exe.lower().endswith(".exe") else exe for exe in self.exes if exe in found]
        name = "、".join(names)
        if name != self.device_name:
            self.device_name = name
            self.on_open(self.kind, name)

    def _mix(self) -> np.ndarray:
        """每一路各拿 0.1 秒出來加在一起；還沒有聲音的那一路當作安靜。"""
        out = np.zeros(BLOCK, dtype=np.float32)
        with self._lock:
            for pid, buf in self._buffers.items():
                take = buf[:BLOCK]
                out[:len(take)] += take
                self._buffers[pid] = buf[BLOCK:]
        return np.clip(out, -1.0, 1.0)

    def _push(self, block: np.ndarray) -> None:
        self.delivered = True
        try:
            self.q.put_nowait(block)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass

    def run(self) -> None:
        try:
            scanned = 0.0
            tick = time.monotonic()
            while not self._halt.is_set():
                now = time.monotonic()
                if now - scanned >= self.rescan:
                    scanned = now
                    self._scan()
                    with self._lock:
                        broken = bool(self.failed) and not self.streams
                        reason = next(iter(self.failed.values()), "")
                    if broken:      # 要聽的程式有開，但每一個都收不到：回報，讓上面改收整台電腦的聲音
                        raise OSError(reason)
                self._push(self._mix())
                tick += BLOCK / SR
                delay = tick - time.monotonic()
                if delay > 0:
                    self._halt.wait(delay)
                elif delay < -1.0:      # 電腦卡了一下：不要一口氣補一堆
                    tick = time.monotonic()
        except Exception as ex:  # noqa: BLE001
            if not self._halt.is_set():
                self.on_error(self.kind, ex)
        finally:
            with self._lock:
                streams = list(self.streams.values())
            for stream in streams:
                stream.stop()


def probe(out_path: str, seconds: float = 2.0) -> int:
    """
    檢查用：收「自己這個程序」的聲音幾秒，把結果寫成 JSON。另外開一個程序來跑，
    就算 Windows 這邊出了嚴重的錯，也不會拖垮主程式。
    """
    import json
    import os
    import tempfile
    import wave

    result: dict = {"available": available(), "opened": False, "error": "", "samples": 0, "peak": 0.0}

    def save() -> None:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f)

    save()
    if not result["available"]:
        return 0
    got: list[np.ndarray] = []
    stream = ProcessStream(os.getpid(), lambda _stream, samples: got.append(samples))
    stream.start()
    stream.opened.wait(8.0)
    result["opened"] = stream.error is None and stream.is_alive()
    save()
    try:      # 自己播一小段聲音，看收不收得到（沒有音效裝置的電腦播不出來，那就只知道開不開得起來）
        import winsound

        tone = (0.3 * 32767 * np.sin(2 * np.pi * 440 * np.arange(44100) / 44100)).astype(np.int16)
        path = os.path.join(tempfile.mkdtemp(prefix="livelingo-probe-"), "tone.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(tone.tobytes())
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        result["played"] = True
    except Exception as ex:  # noqa: BLE001
        result["played"] = describe(ex)
    time.sleep(seconds)
    stream.stop()
    stream.join(3.0)
    if stream.error is not None:
        result["error"] = describe(stream.error)
    heard = np.concatenate(got) if got else np.zeros(0, dtype=np.float32)
    result["samples"] = int(len(heard))
    result["peak"] = round(float(np.abs(heard).max()), 4) if len(heard) else 0.0
    save()
    return 0
