"""
檢查收音：列出這台電腦的播放裝置和麥克風，然後各錄 4 秒，看看有沒有收到聲音。
語音翻譯沒反應的時候執行這個（點兩下 check_audio.bat），把結果截圖給 Claude。
"""

import json
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audio import SR, Capture, list_devices  # noqa: E402


def main() -> None:
    try:
        info = list_devices()
    except Exception as ex:  # noqa: BLE001
        print(f"讀不到音訊裝置：{ex!r}")
        return
    print("播放裝置：")
    for name in info["speakers"]:
        print("  " + ("* " if name == info["default_speaker"] else "  ") + name)
    print("麥克風：")
    for name in info["microphones"]:
        print("  " + ("* " if name == info["default_microphone"] else "  ") + name)
    print("（* 是系統預設）\n")
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}
    chosen = {"loopback": cfg.get("speaker_device"), "mic": cfg.get("mic_device")}

    for kind, hint in (("loopback", "請現在播放一段有聲音的影片或音樂"), ("mic", "請現在對著麥克風說話")):
        errors = []
        cap = Capture(kind, on_error=lambda k, ex: errors.append(ex), device=chosen[kind])
        print(f"測試{'電腦播放的聲音' if kind == 'loopback' else '麥克風'}：{hint}，錄 4 秒…", flush=True)
        cap.start()
        blocks, end = [], time.time() + 4
        while time.time() < end and not errors:
            try:
                blocks.append(cap.q.get(timeout=0.5))
            except queue.Empty:
                pass
        cap.stop()
        if errors:
            print(f"  失敗：{errors[0]!r}\n")
            continue
        audio = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        verdict = "有收到聲音，正常" if peak > 0.01 else "幾乎沒有聲音（音量太小、靜音，或裝置選錯）"
        print(f"  裝置：{cap.device_name}\n  收到 {len(audio) / SR:.1f} 秒，最大音量 {peak:.3f}：{verdict}\n")


if __name__ == "__main__":
    main()
