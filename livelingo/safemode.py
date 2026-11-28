"""安全模式啟動（2.21.0）：開 LiveLingo 的時候按住 Shift（或加 --safe），這次用預設設定開，
語言和聊天區留著，原本的設定留一份可以復原。設定改壞了、快速鍵衝突、浮動字幕出不來的時候用。"""

import os
import sys


def shift_held() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


def requested(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    return "--safe" in argv or bool(os.environ.get("LIVELINGO_SAFE")) or shift_held()
