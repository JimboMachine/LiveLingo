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


# ---- 回話建議的小框（2.9.0）：按快速鍵就跳出來，先顯示「想一下」，建議好了就列出來，按 1／2／3 複製 ----

REPLY_TEXTS = {
    "zh-TW": ("正在想回話…", "按 1／2／3 複製　·　Esc 取消", "想不出建議，請再按一次快速鍵"),
    "zh-CN": ("正在想回话…", "按 1／2／3 复制　·　Esc 取消", "想不出建议，请再按一次快捷键"),
    "en": ("Thinking of replies…", "Press 1 / 2 / 3 to copy  ·  Esc: cancel", "No ideas this time. Press the hotkey again"),
    "ko": ("답장을 생각하는 중…", "1 / 2 / 3: 복사  ·  Esc: 취소", "이번엔 추천이 없어요. 단축키를 다시 눌러 주세요"),
    "ja": ("返信を考えています…", "1／2／3 でコピー　·　Esc：キャンセル", "提案が出ませんでした。もう一度ショートカットを押してください"),
    "vi": ("Đang nghĩ câu trả lời…", "Bấm 1 / 2 / 3 để sao chép  ·  Esc: hủy", "Lần này chưa có gợi ý. Hãy bấm phím tắt lại"),
    "th": ("กำลังคิดคำตอบ…", "กด 1 / 2 / 3 เพื่อคัดลอก  ·  Esc: ยกเลิก", "ครั้งนี้ไม่มีคำแนะนำ กดปุ่มลัดอีกครั้ง"),
    "id": ("Sedang memikirkan balasan…", "Tekan 1 / 2 / 3 untuk menyalin  ·  Esc: batal", "Belum ada ide. Tekan tombol pintas lagi"),
    "es": ("Pensando respuestas…", "Pulsa 1 / 2 / 3 para copiar  ·  Esc: cancelar", "Esta vez no hay ideas. Pulsa el atajo otra vez"),
    "pt": ("Pensando em respostas…", "Aperte 1 / 2 / 3 para copiar  ·  Esc: cancelar", "Sem ideias desta vez. Aperte o atalho de novo"),
    "ru": ("Придумываю ответы…", "1 / 2 / 3 — копировать  ·  Esc — отмена", "Вариантов нет. Нажмите сочетание клавиш ещё раз"),
    "de": ("Antworten werden überlegt…", "1 / 2 / 3 zum Kopieren  ·  Esc: abbrechen", "Diesmal keine Vorschläge. Drücke das Tastenkürzel noch einmal"),
    "fr": ("Recherche de réponses…", "1 / 2 / 3 pour copier  ·  Esc : annuler", "Pas d'idée cette fois. Appuyez de nouveau sur le raccourci"),
}
REPLY_SIZE = (680, 300)


def choose(in_path: Path, out_path: Path, lang: str = "zh-TW", their: str = "ko") -> None:
    """回話建議的小框。主程式把建議寫進 in_path（{"items": [{"text", "meaning"}]}，失敗是 {"items": []}），
    這裡每 0.2 秒看一次；使用者按 1／2／3 或點一下：把 {"text": 選的那句} 寫進 out_path 就收起來。
    跟快速輸入框一樣，不會替使用者在別的程式裡打字或貼上，複製由主程式做。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    waiting, hint, empty = REPLY_TEXTS.get(lang, REPLY_TEXTS["en"])
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=ACCENT)
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    size = (round(REPLY_SIZE[0] * scale), round(REPLY_SIZE[1] * scale))
    x, y = place((0, 0, root.winfo_screenwidth(), root.winfo_screenheight()), size)
    root.geometry(f"{size[0]}x{size[1]}+{x}+{min(y, root.winfo_screenheight() - size[1] - 40)}")
    family = FONTS.get(lang, "Segoe UI")
    panel = tk.Frame(root, bg=PANEL)
    panel.pack(fill="both", expand=True, padx=2, pady=2)
    pad = round(18 * scale)
    body = tk.Frame(panel, bg=PANEL)
    body.pack(fill="both", expand=True, padx=pad, pady=(round(12 * scale), 0))
    status = tk.Label(body, text=waiting, bg=PANEL, fg=DIM, font=(family, 13), anchor="w", justify="left")
    status.pack(fill="x")
    foot = tk.Frame(panel, bg=PANEL)
    foot.pack(fill="x", padx=pad, pady=(0, round(8 * scale)))
    tk.Label(foot, text=hint, bg=PANEL, fg=DIM, font=(family, 10), anchor="w").pack(side="left")
    tk.Label(foot, text="LiveLingo", bg=PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold"), anchor="e").pack(side="right")
    state = {"closed": False, "items": None}
    wrap = size[0] - 2 * pad - round(40 * scale)

    def close(_event=None):
        if not state["closed"]:
            state["closed"] = True
            root.destroy()

    def pick(n: int):
        items = state["items"] or []
        if 0 <= n < len(items):
            out_path.write_text(json.dumps({"text": items[n]["text"]}, ensure_ascii=False), encoding="utf-8")
            close()

    def show(items: list[dict]):
        state["items"] = items
        if not items:
            status.configure(text=empty)
            return
        status.pack_forget()
        for i, item in enumerate(items[:3]):
            row = tk.Frame(body, bg=PANEL, cursor="hand2")
            row.pack(fill="x", pady=(0, round(6 * scale)))
            tk.Label(row, text=str(i + 1), bg=ACCENT, fg=PANEL, font=("Segoe UI", 12, "bold"), width=2).pack(side="left", anchor="n")
            texts = tk.Frame(row, bg=PANEL)
            texts.pack(side="left", fill="x", expand=True, padx=(round(10 * scale), 0))
            a = tk.Label(texts, text=item["text"], bg=PANEL, fg=TEXT, font=(FONTS.get(their, family), 14), anchor="w",
                         justify="left", wraplength=wrap)
            a.pack(fill="x")
            b = tk.Label(texts, text=item.get("meaning", ""), bg=PANEL, fg=DIM, font=(family, 11), anchor="w",
                         justify="left", wraplength=wrap)
            b.pack(fill="x")
            for w in (row, texts, a, b):
                w.bind("<Button-1>", lambda _e, n=i: pick(n))

    def poll():
        if state["closed"]:
            return
        if state["items"] is None:
            try:
                data = json.loads(in_path.read_text(encoding="utf-8"))
                show([i for i in data.get("items", []) if isinstance(i, dict) and i.get("text")])
            except (OSError, ValueError):
                pass
        root.after(200, poll)

    def lost(_event=None):
        root.after(150, lambda: None if state["closed"] or root.focus_displayof() else close())

    for n in range(3):
        root.bind(str(n + 1), lambda _e, n=n: pick(n))
        root.bind(f"<KP_{n + 1}>", lambda _e, n=n: pick(n))
    root.bind("<Escape>", close)
    root.bind("<FocusOut>", lost)
    root.after(30, lambda: (root.lift(), root.focus_force()))
    root.after(50, poll)
    fake = os.environ.get("LIVELINGO_FAKE_REPLY")      # 健康檢查用：建議一出來就自己選這一個（1、2、3）
    if fake:
        def auto():
            if state["items"]:
                pick(int(fake) - 1)
            elif not state["closed"]:
                root.after(200, auto)
        root.after(300, auto)
    root.after(180000, close)      # 三分鐘沒動就自己收掉
    root.mainloop()
