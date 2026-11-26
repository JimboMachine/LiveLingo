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


# ---- 翻譯結果小卡（2.11.0）：翻選取的字、框選翻譯用。在滑鼠旁邊顯示，不搶遊戲的焦點；點一下或 Esc、或過幾秒自己收 ----

CARD_TEXTS = {
    "zh-TW": ("翻譯中…", "點一下或按 Esc 關閉", "（翻不出來）"),
    "zh-CN": ("翻译中…", "点一下或按 Esc 关闭", "（翻不出来）"),
    "en": ("Translating…", "Click or press Esc to close", "(could not translate)"),
    "ko": ("번역하는 중…", "클릭하거나 Esc로 닫기", "(번역하지 못했습니다)"),
    "ja": ("翻訳中…", "クリックまたは Esc で閉じる", "（翻訳できませんでした）"),
    "vi": ("Đang dịch…", "Bấm hoặc nhấn Esc để đóng", "(không dịch được)"),
    "th": ("กำลังแปล…", "คลิกหรือกด Esc เพื่อปิด", "(แปลไม่ได้)"),
    "id": ("Menerjemahkan…", "Klik atau tekan Esc untuk menutup", "(tidak dapat diterjemahkan)"),
    "es": ("Traduciendo…", "Haz clic o pulsa Esc para cerrar", "(no se pudo traducir)"),
    "pt": ("Traduzindo…", "Clique ou aperte Esc para fechar", "(não foi possível traduzir)"),
    "ru": ("Перевожу…", "Щёлкните или нажмите Esc, чтобы закрыть", "(не удалось перевести)"),
    "de": ("Wird übersetzt…", "Klicken oder Esc zum Schließen", "(nicht übersetzbar)"),
    "fr": ("Traduction…", "Cliquez ou appuyez sur Esc pour fermer", "(traduction impossible)"),
}
CARD_WIDTH = 520


def card(in_path: Path, lang: str = "zh-TW", their: str = "ko") -> None:
    """翻譯結果的小卡。主程式把結果寫進 in_path（{"items": [{"original", "translated"}], "done": 是不是都翻完了}），
    這裡每 0.2 秒看一次、有變就更新。翻完之後依字數停留幾秒就自己收掉；點一下或 Esc 馬上收。不拿焦點，遊戲照常能操作。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    waiting, hint, failed = CARD_TEXTS.get(lang, CARD_TEXTS["en"])
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=ACCENT)
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    family = FONTS.get(lang, "Segoe UI")
    panel = tk.Frame(root, bg=PANEL)
    panel.pack(fill="both", expand=True, padx=2, pady=2)
    pad = round(14 * scale)
    wrap = round(CARD_WIDTH * scale) - 2 * pad
    body = tk.Frame(panel, bg=PANEL)
    body.pack(fill="both", expand=True, padx=pad, pady=(round(10 * scale), 0))
    status = tk.Label(body, text=waiting, bg=PANEL, fg=DIM, font=(family, 13), anchor="w", justify="left")
    status.pack(fill="x")
    foot = tk.Frame(panel, bg=PANEL)
    foot.pack(fill="x", padx=pad, pady=(0, round(6 * scale)))
    tk.Label(foot, text=hint, bg=PANEL, fg=DIM, font=(family, 9), anchor="w").pack(side="left")
    tk.Label(foot, text="LiveLingo", bg=PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), anchor="e").pack(side="right")
    state = {"closed": False, "shown": None, "placed": False, "done_at": None}

    def close(_event=None):
        if not state["closed"]:
            state["closed"] = True
            root.destroy()

    def place_near_pointer():
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        px, py = root.winfo_pointerxy()
        x = min(max(0, px + round(16 * scale)), max(0, root.winfo_screenwidth() - w - 8))
        y = py + round(18 * scale)
        if y + h > root.winfo_screenheight() - 8:      # 下面放不下就放在滑鼠上面
            y = max(0, py - h - round(18 * scale))
        root.geometry(f"+{x}+{y}")

    def show(data: dict):
        items = [i for i in data.get("items", []) if isinstance(i, dict)]
        for child in list(body.winfo_children()):
            child.destroy()
        if not items:
            tk.Label(body, text=waiting, bg=PANEL, fg=DIM, font=(family, 13), anchor="w").pack(fill="x")
        for item in items[:8]:
            original, translated = str(item.get("original", "")), str(item.get("translated", ""))
            tk.Label(body, text=original, bg=PANEL, fg=DIM, font=(FONTS.get(their, family), 10), anchor="w", justify="left",
                     wraplength=wrap).pack(fill="x")
            row = tk.Label(body, text=translated or (waiting if not data.get("done") else failed), bg=PANEL,
                           fg=TEXT if translated else DIM, font=(family, 14), anchor="w", justify="left", wraplength=wrap)
            row.pack(fill="x", pady=(0, round(8 * scale)))
        for widget in (root, panel, body, foot):
            widget.bind("<Button-1>", close)
        for child in body.winfo_children():
            child.bind("<Button-1>", close)
        place_near_pointer()

    def poll():
        if state["closed"]:
            return
        try:
            raw = in_path.read_text(encoding="utf-8")
            if raw != state["shown"]:
                state["shown"] = raw
                data = json.loads(raw)
                show(data)
                if data.get("done") and state["done_at"] is None:
                    chars = sum(len(str(i.get("translated", ""))) for i in data.get("items", []))
                    state["done_at"] = 500 if os.environ.get("LIVELINGO_FAKE_CARD") else min(30000, 6000 + 90 * chars)      # 讀完要的時間：字越多停越久（健康檢查用：很快收）
                    root.after(state["done_at"], close)
        except (OSError, ValueError):
            pass
        root.after(200, poll)

    root.bind("<Escape>", close)
    root.after(50, poll)
    root.after(0, place_near_pointer)
    root.after(120000, close)      # 兩分鐘還沒翻完（翻譯引擎出問題）就自己收掉
    root.mainloop()


# ---- 常用句庫的小框（2.11.0）：按快速鍵跳出來，按 1 到 9 複製那一句翻好的話 ----

PHRASE_TEXTS = {
    "zh-TW": ("按 1–9 複製　·　←／→ 換頁　·　Esc 取消", "（翻譯中…）"),
    "zh-CN": ("按 1–9 复制　·　←／→ 翻页　·　Esc 取消", "（翻译中…）"),
    "en": ("Press 1–9 to copy  ·  ←/→ page  ·  Esc: cancel", "(translating…)"),
    "ko": ("1–9: 복사  ·  ←/→ 페이지  ·  Esc: 취소", "(번역 중…)"),
    "ja": ("1–9 でコピー　·　←／→ ページ　·　Esc：キャンセル", "（翻訳中…）"),
    "vi": ("Bấm 1–9 để sao chép  ·  ←/→ trang  ·  Esc: hủy", "(đang dịch…)"),
    "th": ("กด 1–9 เพื่อคัดลอก  ·  ←/→ เปลี่ยนหน้า  ·  Esc: ยกเลิก", "(กำลังแปล…)"),
    "id": ("Tekan 1–9 untuk menyalin  ·  ←/→ halaman  ·  Esc: batal", "(menerjemahkan…)"),
    "es": ("Pulsa 1–9 para copiar  ·  ←/→ página  ·  Esc: cancelar", "(traduciendo…)"),
    "pt": ("Aperte 1–9 para copiar  ·  ←/→ página  ·  Esc: cancelar", "(traduzindo…)"),
    "ru": ("1–9 — копировать  ·  ←/→ страница  ·  Esc — отмена", "(перевожу…)"),
    "de": ("1–9 zum Kopieren  ·  ←/→ Seite  ·  Esc: abbrechen", "(wird übersetzt…)"),
    "fr": ("1–9 pour copier  ·  ←/→ page  ·  Esc : annuler", "(traduction…)"),
}
PHRASE_SIZE = (640, 420)


def phrases(in_path: Path, out_path: Path, lang: str = "zh-TW", their: str = "ko") -> None:
    """常用句庫的小框。主程式把句子寫進 in_path（{"items": [{"text", "tr"}]}，tr 還沒翻好是空的），這裡每 0.3 秒看一次、有變就更新。
    按 1 到 9（或點一下）：把 {"text": 那一句的譯文} 寫進 out_path 就收起來；←／→ 或 PageUp／PageDown 換頁。複製由主程式做。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    hint, pending = PHRASE_TEXTS.get(lang, PHRASE_TEXTS["en"])
    per = 9
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=ACCENT)
    scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    size = (round(PHRASE_SIZE[0] * scale), round(PHRASE_SIZE[1] * scale))
    x, y = place((0, 0, root.winfo_screenwidth(), root.winfo_screenheight()), size)
    root.geometry(f"{size[0]}x{size[1]}+{x}+{max(0, min(y, root.winfo_screenheight() - size[1] - 40))}")
    family = FONTS.get(lang, "Segoe UI")
    panel = tk.Frame(root, bg=PANEL)
    panel.pack(fill="both", expand=True, padx=2, pady=2)
    pad = round(18 * scale)
    body = tk.Frame(panel, bg=PANEL)
    body.pack(fill="both", expand=True, padx=pad, pady=(round(10 * scale), 0))
    foot = tk.Frame(panel, bg=PANEL)
    foot.pack(fill="x", padx=pad, pady=(0, round(8 * scale)))
    tk.Label(foot, text=hint, bg=PANEL, fg=DIM, font=(family, 10), anchor="w").pack(side="left")
    pages = tk.Label(foot, text="", bg=PANEL, fg=DIM, font=(family, 10), anchor="e")
    pages.pack(side="right", padx=(0, 12))
    tk.Label(foot, text="LiveLingo", bg=PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold"), anchor="e").pack(side="right")
    state = {"closed": False, "items": [], "page": 0, "raw": None}
    wrap = size[0] - 2 * pad - round(50 * scale)

    def close(_event=None):
        if not state["closed"]:
            state["closed"] = True
            root.destroy()

    def pick(n: int):
        items = state["items"][state["page"] * per:(state["page"] + 1) * per]
        if 0 <= n < len(items) and items[n].get("tr"):
            out_path.write_text(json.dumps({"text": items[n]["tr"]}, ensure_ascii=False), encoding="utf-8")
            close()

    def draw():
        for child in list(body.winfo_children()):
            child.destroy()
        count = len(state["items"])
        last = max(0, (count - 1) // per)
        state["page"] = min(state["page"], last)
        pages.configure(text=f"{state['page'] + 1}/{last + 1}" if last else "")
        for i, item in enumerate(state["items"][state["page"] * per:(state["page"] + 1) * per]):
            row = tk.Frame(body, bg=PANEL, cursor="hand2")
            row.pack(fill="x", pady=(0, round(5 * scale)))
            tk.Label(row, text=str(i + 1), bg=ACCENT if item.get("tr") else DIM, fg=PANEL, font=("Segoe UI", 11, "bold"), width=2).pack(side="left", anchor="n")
            texts = tk.Frame(row, bg=PANEL)
            texts.pack(side="left", fill="x", expand=True, padx=(round(10 * scale), 0))
            a = tk.Label(texts, text=item.get("tr") or pending, bg=PANEL, fg=TEXT if item.get("tr") else DIM, font=(FONTS.get(their, family), 13),
                         anchor="w", justify="left", wraplength=wrap)
            a.pack(fill="x")
            b = tk.Label(texts, text=item.get("text", ""), bg=PANEL, fg=DIM, font=(family, 10), anchor="w", justify="left", wraplength=wrap)
            b.pack(fill="x")
            for w in (row, texts, a, b):
                w.bind("<Button-1>", lambda _e, n=i: pick(n))

    def turn(step: int):
        count = len(state["items"])
        state["page"] = max(0, min(max(0, (count - 1) // per), state["page"] + step))
        draw()

    def poll():
        if state["closed"]:
            return
        try:
            raw = in_path.read_text(encoding="utf-8")
            if raw != state["raw"]:
                state["raw"] = raw
                state["items"] = [i for i in json.loads(raw).get("items", []) if isinstance(i, dict) and i.get("text")]
                draw()
        except (OSError, ValueError):
            pass
        root.after(300, poll)

    def lost(_event=None):
        root.after(150, lambda: None if state["closed"] or root.focus_displayof() else close())

    for n in range(9):
        root.bind(str(n + 1), lambda _e, n=n: pick(n))
        root.bind(f"<KP_{n + 1}>", lambda _e, n=n: pick(n))
    root.bind("<Right>", lambda _e: turn(1))
    root.bind("<Next>", lambda _e: turn(1))
    root.bind("<Left>", lambda _e: turn(-1))
    root.bind("<Prior>", lambda _e: turn(-1))
    root.bind("<Escape>", close)
    root.bind("<FocusOut>", lost)
    root.after(30, lambda: (root.lift(), root.focus_force()))
    root.after(40, poll)
    fake = os.environ.get("LIVELINGO_FAKE_PHRASE")      # 健康檢查用：句子一出來就自己選這一個（1 到 9）
    if fake:
        def auto():
            if state["items"]:
                pick(int(fake) - 1)
            elif not state["closed"]:
                root.after(200, auto)
        root.after(300, auto)
    root.after(180000, close)      # 三分鐘沒動就自己收掉
    root.mainloop()
