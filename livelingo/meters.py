"""設定頁的音量條：每個播放裝置和麥克風各收一點聲音，算出現在的音量。"""

import queue
import threading
import time

import numpy as np


class LevelMeters:
    def __init__(self, audio, devices: dict, on_levels):
        self.audio = audio
        self.devices = devices
        self.on_levels = on_levels          # on_levels({"speakers": {id: 0~1 或 None}, "microphones": {...}})
        self._halt = threading.Event()
        self._captures: list = []
        self._levels = {"speakers": {}, "microphones": {}}
        self._lock = threading.Lock()

    def start(self) -> None:
        for group, kind in (("speakers", "loopback"), ("microphones", "mic")):
            for dev in self.devices.get(group, [])[:8]:
                self._open(group, kind, dev["id"])
        threading.Thread(target=self._report, daemon=True, name="meters").start()

    def _open(self, group: str, kind: str, dev_id: str) -> None:
        self._levels[group][dev_id] = 0.0

        def failed(_kind, _ex, group=group, dev_id=dev_id):
            with self._lock:
                self._levels[group][dev_id] = None     # 這個裝置收不到聲音

        try:
            cap = self.audio.capture(kind, dev_id, on_error=failed)
        except Exception:  # noqa: BLE001
            self._levels[group][dev_id] = None
            return
        self._captures.append(cap)
        cap.start()
        threading.Thread(target=self._read, args=(cap, group, dev_id), daemon=True).start()

    def _read(self, cap, group: str, dev_id: str) -> None:
        peak = 0.0
        while not self._halt.is_set():
            try:
                block = cap.q.get(timeout=0.3)
            except queue.Empty:
                if not cap.is_alive():
                    return
                peak *= 0.5
                with self._lock:
                    if self._levels[group][dev_id] is not None:
                        self._levels[group][dev_id] = peak
                continue
            rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
            # 換算成 0~1：-60 dB 以下當作沒聲音，-10 dB 以上當作滿格
            db = 20 * np.log10(max(rms, 1e-6))
            level = float(min(1.0, max(0.0, (db + 60) / 50)))
            peak = max(level, peak * 0.7)
            with self._lock:
                if self._levels[group][dev_id] is not None:
                    self._levels[group][dev_id] = round(peak, 3)

    def _report(self) -> None:
        while not self._halt.is_set():
            time.sleep(0.2)
            with self._lock:
                snapshot = {g: dict(v) for g, v in self._levels.items()}
            self.on_levels(snapshot)

    def stop(self) -> None:
        self._halt.set()
        for cap in self._captures:
            cap.stop()
