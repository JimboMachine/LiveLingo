"""
快速輸入框：按快速鍵跳出一個小小的輸入框，打完按 Enter 就把這句話交回主程式去翻譯並複製，輸入框自己消失。
跟框選聊天區一樣是另外開的小程式，結果寫進檔案就結束。它只收你在這個框裡打的字，不會替你在別的程式裡打字或貼上。
"""

import json
import os
import sys
import tkinter as tk
from pathlib import Path

from .region import ACCENT, FONTS, PANEL, TEXT

HINTS = {
    "zh-TW": "Enter 翻譯並複製　·　Esc 取消",
    "zh-CN": "Enter 翻译并复制　·　Esc 取消",
    "en": "Enter: translate and copy  ·  Esc: cancel",
    "ko": "Enter: 번역 후 복사  ·  Esc: 취소",
    "ja": "Enter：翻訳してコピー　·　Esc：キャンセル",
    "vi": "Enter: dịch và sao chép  ·  Esc: hủy",
    "th": "Enter: แปลและคัดลอก  ·  Esc: ยกเลิก",
    "id": "Enter: terjemahkan dan salin  ·  Esc: batal",
    "es": "Enter: traducir y copiar  ·  Esc: cancelar",
    "pt": "Enter: traduzir e copiar  ·  Esc: cancelar",
    "ru": "Enter: перевести и скопировать  ·  Esc: отмена",
    "de": "Enter: übersetzen und kopieren  ·  Esc: abbrechen",
    "fr": "Enter : traduire et copier  ·  Esc : annuler",
}
DIM = "#9AA3AE"
SIZE = (640, 96)


def place(screen: tuple[int, int, int, int], size: tuple[int, int] = SIZE) -> tuple[int, int]:
    """輸入框放在螢幕下方三分之一、左右置中的位置（screen：左、上、寬、高）。"""
    left, top, width, height = screen
    return left + (width - size[0]) // 2, top + int(height * 0.66)


def ask(out_path: Path, lang: str = "zh-TW") -> None:
    """跳出輸入框。按 Enter：把 {"text": 打的字} 寫進 out_path；按 Esc 或點到別的地方：什麼都不寫。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=ACCENT)
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)      # 螢幕放大 150% 時字會跟著變大，框也要跟著放大才裝得下
    size = (round(SIZE[0] * scale), round(SIZE[1] * scale))
    x, y = place((0, 0, root.winfo_screenwidth(), root.winfo_screenheight()), size)
    root.geometry(f"{size[0]}x{size[1]}+{x}+{y}")
    family = FONTS.get(lang, "Segoe UI")
    panel = tk.Frame(root, bg=PANEL)
    panel.pack(fill="both", expand=True, padx=2, pady=2)      # 外面留兩個像素，就是那圈強調色的邊
    pad = round(18 * scale)
    entry = tk.Entry(panel, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=(family, 18), highlightthickness=0)
    entry.pack(fill="x", padx=pad, pady=(round(14 * scale), round(4 * scale)))
    foot = tk.Frame(panel, bg=PANEL)
    foot.pack(fill="x", padx=pad)
    tk.Label(foot, text=HINTS.get(lang, HINTS["en"]), bg=PANEL, fg=DIM, font=(family, 10), anchor="w").pack(side="left")
    tk.Label(foot, text="LiveLingo", bg=PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold"), anchor="e").pack(side="right")
    state = {"closed": False}

    def close(_event=None):
        if not state["closed"]:
            state["closed"] = True
            root.destroy()

    def send(_event=None):
        text = entry.get().strip()
        if text:
            out_path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        close()

    def lost(_event=None):      # 點回遊戲了：收起來，不要留一個框擋在畫面上
        root.after(150, lambda: None if state["closed"] or root.focus_displayof() else close())

    entry.bind("<Return>", send)
    root.bind("<Escape>", close)
    root.bind("<FocusOut>", lost)

    def focus():
        root.lift()
        root.focus_force()
        entry.focus_set()

    root.after(30, focus)
    fake = os.environ.get("LIVELINGO_FAKE_QUICK")      # 健康檢查用：自己填字、自己送出，確認這個框開得起來
    if fake:
        root.after(300, lambda: (entry.insert(0, fake), send()))
    root.after(120000, close)      # 兩分鐘沒動就自己收掉
    root.mainloop()
