"""
開發和測試用：不需要 Windows、不需要真的音效卡，就能把整個程式跑起來，用瀏覽器看介面。

    LLAMA_SERVER=/path/to/llama-server LIVELINGO_DATA=/path/to/data python tests/devserver.py

資料夾裡要先放好模型（models/translategemma-4b.gguf、models/ocr、models/whisper）。
音訊裝置是假的：「聽對方說話」會播放 LIVELINGO_FAKE_HEARD 指定的錄音，「聽我說話」播放 LIVELINGO_FAKE_SPOKEN。
"""

import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livelingo.core.audio import SR, ArrayCapture  # noqa: E402
from livelingo.paths import data_dir  # noqa: E402
from livelingo.server import Server  # noqa: E402
from livelingo.service import Service  # noqa: E402


def load_audio(path: str | None) -> np.ndarray:
    if not path:
        return np.zeros(SR, dtype=np.float32)
    import av

    out = []
    with av.open(path) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
        for frame in container.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
    audio = np.concatenate(out).astype(np.float32)
    loud = np.flatnonzero(np.abs(audio) > 0.05)      # 錄音開頭常有幾秒空白，直接從有人說話的地方開始
    return audio[max(0, int(loud[0]) - SR // 4):] if len(loud) else audio


class FakeAudio:
    def __init__(self):
        self.heard = load_audio(os.environ.get("LIVELINGO_FAKE_HEARD"))
        self.spoken = load_audio(os.environ.get("LIVELINGO_FAKE_SPOKEN"))
        self.opened: list[tuple[str, str | None]] = []

    def devices(self) -> dict:
        return {
            "speakers": [
                {"id": "spk-headset", "name": "耳機 (HyperX Cloud II)", "default": False},
                {"id": "spk-monitor", "name": "MSI G274QPF (NVIDIA High Definition Audio)", "default": True},
                {"id": "spk-speaker", "name": "喇叭 (Realtek(R) Audio)", "default": False}],
            "microphones": [
                {"id": "mic-headset", "name": "麥克風 (HyperX Cloud II)", "default": True},
                {"id": "mic-webcam", "name": "麥克風 (HD Webcam C270)", "default": False}],
        }

    def capture(self, kind, device, on_error=None, on_open=None):
        self.opened.append((kind, device))
        if device == "spk-speaker":       # 模擬一個打不開的裝置
            cap = ArrayCapture(kind, np.zeros(SR, dtype=np.float32))

            def run():
                time.sleep(0.3)
                if on_error:
                    on_error(kind, RuntimeError("device is busy"))

            cap.run = run
            return cap
        silent = device in ("spk-monitor", "mic-webcam")
        audio = np.zeros(SR, dtype=np.float32) if silent else (self.heard if kind == "loopback" else self.spoken)
        cap = ArrayCapture(kind, np.concatenate([np.zeros(SR // 2, dtype=np.float32), audio]))
        names = {d["id"]: d["name"] for group in self.devices().values() for d in group}
        cap.device_name = names.get(device) or ("MSI G274QPF (NVIDIA High Definition Audio)" if kind == "loopback"
                                                else "麥克風 (HyperX Cloud II)")
        if on_open:
            real_run = cap.run

            def run():
                on_open(kind, cap.device_name)
                real_run()

            cap.run = run
        return cap


def main() -> None:
    service = Service(data_dir(), audio=FakeAudio())
    shot = os.environ.get("LIVELINGO_FAKE_SCREEN")      # 一張圖片，當成螢幕上框選的那一塊
    if shot:
        import cv2

        service.grab = lambda region: cv2.imread(shot)
    server = Server(service, port=int(os.environ.get("LIVELINGO_PORT", "0")))
    server.token = os.environ.get("LIVELINGO_TOKEN", server.token)
    server.start()
    print(f"http://127.0.0.1:{server.port}/?t={server.token}", flush=True)
    service.on_quit = lambda: os._exit(0)
    service.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.shutdown()


if __name__ == "__main__":
    main()
