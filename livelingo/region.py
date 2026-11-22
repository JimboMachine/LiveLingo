"""框選聊天區：整個螢幕變暗，用滑鼠拖一個框。這是另外開的小程式，框完把結果寫進檔案就結束。"""

import json
import sys
import tkinter as tk
from pathlib import Path

HINTS = {
    "zh-TW": "按住滑鼠左鍵，把聊天訊息出現的範圍框起來（按 Esc 取消）",
    "zh-CN": "按住鼠标左键，把聊天消息出现的范围框起来（按 Esc 取消）",
    "en": "Hold the left mouse button and drag a box around the chat messages (Esc to cancel)",
    "ko": "마우스 왼쪽 버튼을 누른 채로 채팅 메시지가 보이는 영역을 드래그하세요 (Esc: 취소)",
    "ja": "マウスの左ボタンを押したまま、チャットのメッセージが出る範囲を囲んでください（Esc でキャンセル）",
}
FONTS = {"zh-TW": "Microsoft JhengHei UI", "zh-CN": "Microsoft YaHei UI", "ko": "Malgun Gothic", "ja": "Yu Gothic UI", "en": "Segoe UI"}


ACCENT, PANEL, TEXT = "#4ADE9A", "#171A1E", "#F2F4F6"      # 跟主程式一樣的顏色


def _rounded(canvas, x1, y1, x2, y2, r, **kw):
    """圓角長方形（tkinter 沒有內建，用平滑的多邊形畫）。"""
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
              x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


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
        root.attributes("-alpha", 0.45)
    except tk.TclError:
        pass
    left, top, width, height = virtual_screen()
    if width and height:
        root.geometry(f"{width}x{height}+{left}+{top}")
    else:
        left = top = 0
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    canvas = tk.Canvas(root, bg="#0F1113", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    family = FONTS.get(lang, "Segoe UI")
    # 說明放在主螢幕上方正中間的圓角框裡，跟主程式同一套顏色
    hint = canvas.create_text(0, 0, anchor="n", fill=TEXT, font=(family, 17, "bold"), text=HINTS.get(lang, HINTS["en"]),
                              width=max(320, root.winfo_screenwidth() - 240), justify="center")
    canvas.coords(hint, -min(left, 0) + root.winfo_screenwidth() // 2, -min(top, 0) + 56)
    x1, y1, x2, y2 = canvas.bbox(hint)
    panel = _rounded(canvas, x1 - 28, y1 - 16, x2 + 28, y2 + 16, 18, fill=PANEL, outline=ACCENT, width=2)
    canvas.tag_lower(panel, hint)
    state: dict = {}

    def press(e):
        state["x"], state["y"] = e.x, e.y
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=ACCENT, width=3, fill="#2A5A46", stipple="gray25")
        state["size_bg"] = canvas.create_rectangle(0, 0, 0, 0, fill=PANEL, outline=ACCENT)
        state["size"] = canvas.create_text(0, 0, anchor="nw", fill=TEXT, font=(family, 12, "bold"), text="")

    def drag(e):
        if "rect" not in state:
            return
        canvas.coords(state["rect"], state["x"], state["y"], e.x, e.y)
        # 框的右下角標出目前的大小
        canvas.itemconfigure(state["size"], text=f"{abs(e.x - state['x'])} × {abs(e.y - state['y'])}")
        canvas.coords(state["size"], max(e.x, state["x"]) - 96, max(e.y, state["y"]) + 12)
        bx1, by1, bx2, by2 = canvas.bbox(state["size"])
        canvas.coords(state["size_bg"], bx1 - 8, by1 - 4, bx2 + 8, by2 + 4)

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
