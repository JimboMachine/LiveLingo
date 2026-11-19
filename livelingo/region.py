"""框選聊天區：整個螢幕變暗，用滑鼠拖一個框。這是另外開的小程式，框完把結果寫進檔案就結束。"""

import json
import sys
import tkinter as tk
from pathlib import Path

HINTS = {
    "zh-TW": "按住滑鼠左鍵，把聊天訊息出現的範圍框起來（按 Esc 取消）",
    "en": "Hold the left mouse button and drag a box around the chat messages (Esc to cancel)",
    "ko": "마우스 왼쪽 버튼을 누른 채로 채팅 메시지가 보이는 영역을 드래그하세요 (Esc: 취소)",
    "ja": "マウスの左ボタンを押したまま、チャットのメッセージが出る範囲を囲んでください（Esc でキャンセル）",
}
FONTS = {"zh-TW": "Microsoft JhengHei UI", "ko": "Malgun Gothic", "ja": "Yu Gothic UI", "en": "Segoe UI"}


def virtual_screen() -> tuple[int, int, int, int]:
    """所有螢幕合起來的範圍 (left, top, width, height)。"""
    if sys.platform == "win32":
        import ctypes

        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return 0, 0, 0, 0


def select(out_path: Path, lang: str = "zh-TW") -> None:
    if sys.platform == "win32":
        try:  # 讓框選的座標和螢幕擷取的座標一致（高 DPI 螢幕需要）
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.35)
    except tk.TclError:
        pass
    left, top, width, height = virtual_screen()
    if width and height:
        root.geometry(f"{width}x{height}+{left}+{top}")
    else:
        left = top = 0
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    canvas = tk.Canvas(root, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(40 - min(left, 0), 40 - min(top, 0), anchor="nw", fill="white",
                       font=(FONTS.get(lang, "Segoe UI"), 20), text=HINTS.get(lang, HINTS["en"]))
    state: dict = {}

    def press(e):
        state["x"], state["y"] = e.x, e.y
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#4ADE9A", width=3)

    def drag(e):
        if "rect" in state:
            canvas.coords(state["rect"], state["x"], state["y"], e.x, e.y)

    def release(e):
        if "rect" not in state:
            return
        x1, x2 = sorted((state["x"], e.x))
        y1, y2 = sorted((state["y"], e.y))
        region = {"left": left + x1, "top": top + y1, "width": x2 - x1, "height": y2 - y1}
        out_path.write_text(json.dumps(region), encoding="utf-8")
        root.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    root.bind("<Escape>", lambda e: root.destroy())
    root.after(100, root.focus_force)
    root.mainloop()
