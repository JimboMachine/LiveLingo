"""框選聊天區：整個螢幕變暗，用滑鼠拖一個框；沒有滑鼠也可以，方向鍵移動、Shift＋方向鍵改大小、Enter 確定。
這是另外開的小程式，框完把結果寫進檔案就結束。"""

import json
import sys
import tkinter as tk
from pathlib import Path

HINTS = {
    "zh-TW": "按住滑鼠左鍵，把聊天訊息出現的範圍框起來（按 Esc 取消）\n用鍵盤：方向鍵移動，Shift＋方向鍵改大小，Enter 確定",
    "zh-CN": "按住鼠标左键，把聊天消息出现的范围框起来（按 Esc 取消）\n用键盘：方向键移动，Shift＋方向键改大小，Enter 确定",
    "en": "Hold the left mouse button and drag a box around the chat messages (Esc to cancel)\nKeyboard: arrow keys move, Shift + arrow keys resize, Enter confirms",
    "ko": "마우스 왼쪽 버튼을 누른 채로 채팅 메시지가 보이는 영역을 드래그하세요 (Esc: 취소)\n키보드: 방향키로 이동, Shift+방향키로 크기 조절, Enter로 확정",
    "ja": "マウスの左ボタンを押したまま、チャットのメッセージが出る範囲を囲んでください（Esc でキャンセル）\nキーボード：矢印キーで移動、Shift＋矢印キーでサイズ変更、Enter で決定",
    "vi": "Giữ chuột trái và kéo một khung quanh tin nhắn chat (Esc để hủy)\nBàn phím: phím mũi tên để di chuyển, Shift + phím mũi tên để đổi kích thước, Enter để xác nhận",
    "th": "กดเมาส์ซ้ายค้างไว้แล้วลากกรอบรอบข้อความแชท (Esc เพื่อยกเลิก)\nแป้นพิมพ์: ปุ่มลูกศรเพื่อย้าย Shift + ปุ่มลูกศรเพื่อปรับขนาด Enter เพื่อยืนยัน",
    "id": "Tahan tombol kiri mouse dan seret kotak di sekitar pesan chat (Esc untuk batal)\nKeyboard: tombol panah untuk memindahkan, Shift + panah untuk mengubah ukuran, Enter untuk konfirmasi",
    "es": "Mantén pulsado el botón izquierdo y arrastra un recuadro alrededor de los mensajes del chat (Esc para cancelar)\nTeclado: las flechas mueven, Shift + flechas cambian el tamaño, Enter confirma",
    "pt": "Segure o botão esquerdo do mouse e arraste um quadro em volta das mensagens do chat (Esc para cancelar)\nTeclado: setas movem, Shift + setas mudam o tamanho, Enter confirma",
    "ru": "Удерживая левую кнопку мыши, обведите рамкой сообщения чата (Esc для отмены)\nКлавиатура: стрелки перемещают, Shift + стрелки меняют размер, Enter подтверждает",
    "de": "Linke Maustaste gedrückt halten und einen Rahmen um die Chat-Nachrichten ziehen (Esc zum Abbrechen)\nTastatur: Pfeiltasten verschieben, Shift + Pfeiltasten ändern die Größe, Enter bestätigt",
    "fr": "Maintenez le bouton gauche de la souris et tracez un cadre autour des messages du chat (Esc pour annuler)\nClavier : les flèches déplacent, Shift + flèches redimensionnent, Enter valide",
}
# 框選翻譯（只翻一次）用的說明：第一行不一樣，第二行（用鍵盤）跟框聊天區一樣
SNAP_FIRST = {
    "zh-TW": "按住滑鼠左鍵，框住要翻的字（按 Esc 取消）",
    "zh-CN": "按住鼠标左键，框住要翻的字（按 Esc 取消）",
    "en": "Hold the left mouse button and drag a box around the text to translate (Esc to cancel)",
    "ko": "마우스 왼쪽 버튼을 누른 채로 번역할 글자를 드래그해서 감싸세요 (Esc: 취소)",
    "ja": "マウスの左ボタンを押したまま、翻訳したい文字を囲んでください（Esc でキャンセル）",
    "vi": "Giữ chuột trái và kéo một khung quanh chữ cần dịch (Esc để hủy)",
    "th": "กดเมาส์ซ้ายค้างไว้แล้วลากกรอบรอบข้อความที่ต้องการแปล (Esc เพื่อยกเลิก)",
    "id": "Tahan tombol kiri mouse dan seret kotak di sekitar teks yang ingin diterjemahkan (Esc untuk batal)",
    "es": "Mantén pulsado el botón izquierdo y arrastra un recuadro alrededor del texto que quieres traducir (Esc para cancelar)",
    "pt": "Segure o botão esquerdo do mouse e arraste um quadro em volta do texto a traduzir (Esc para cancelar)",
    "ru": "Удерживая левую кнопку мыши, обведите рамкой текст для перевода (Esc для отмены)",
    "de": "Linke Maustaste gedrückt halten und einen Rahmen um den zu übersetzenden Text ziehen (Esc zum Abbrechen)",
    "fr": "Maintenez le bouton gauche de la souris et tracez un cadre autour du texte à traduire (Esc pour annuler)",
}
SNAP_HINTS = {lang: SNAP_FIRST[lang] + "\n" + HINTS[lang].split("\n")[1] for lang in HINTS}
STEP = 20                 # 方向鍵按一下動幾個像素
SMALLEST = (60, 30)       # 用鍵盤縮小的時候，框最小這麼大
FONTS = {"zh-TW": "Microsoft JhengHei UI", "zh-CN": "Microsoft YaHei UI", "ko": "Malgun Gothic", "ja": "Yu Gothic UI", "en": "Segoe UI", "vi": "Segoe UI", "th": "Leelawadee UI", "id": "Segoe UI", "es": "Segoe UI", "pt": "Segoe UI", "ru": "Segoe UI", "de": "Segoe UI", "fr": "Segoe UI"}


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


def first_box(width: int, height: int, off_x: int = 0, off_y: int = 0) -> tuple[int, int, int, int]:
    """還沒有框的時候按方向鍵：先在主螢幕中間放一個（寬四成、高四分之一），再用方向鍵調。"""
    w, h = int(width * 0.4), int(height * 0.25)
    x, y = off_x + (width - w) // 2, off_y + (height - h) // 2
    return x, y, x + w, y + h


def nudge(box: tuple[int, int, int, int], dx: int, dy: int, grow: bool, width: int, height: int) -> tuple[int, int, int, int]:
    """方向鍵動一下之後的框。grow 是改大小（動右下角），不然是整個搬。不會跑出畫面，也不會縮到看不見。"""
    x1, x2 = sorted((box[0], box[2]))
    y1, y2 = sorted((box[1], box[3]))
    if grow:
        x2 = max(x1 + SMALLEST[0], min(width, x2 + dx))
        y2 = max(y1 + SMALLEST[1], min(height, y2 + dy))
    else:
        dx = max(-x1, min(dx, width - x2))
        dy = max(-y1, min(dy, height - y2))
        x1, y1, x2, y2 = x1 + dx, y1 + dy, x2 + dx, y2 + dy
    return x1, y1, x2, y2


def select(out_path: Path, lang: str = "zh-TW", mode: str = "chat") -> None:
    """mode：chat = 框聊天區（存起來一直讀）；snap = 框一次只翻這一次的字。"""
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
    hint = canvas.create_text(0, 0, anchor="n", fill=TEXT, font=(family, 17, "bold"), text=(SNAP_HINTS if mode == "snap" else HINTS).get(lang, HINTS["en"]),
                              width=max(320, root.winfo_screenwidth() - 240), justify="center")
    canvas.coords(hint, -min(left, 0) + root.winfo_screenwidth() // 2, -min(top, 0) + 56)
    x1, y1, x2, y2 = canvas.bbox(hint)
    panel = _rounded(canvas, x1 - 28, y1 - 16, x2 + 28, y2 + 16, 18, fill=PANEL, outline=ACCENT, width=2)
    canvas.tag_lower(panel, hint)
    state: dict = {}

    def show(x1, y1, x2, y2):
        """把框畫在這裡，右下角標出目前的大小。"""
        if "rect" not in state:
            state["rect"] = canvas.create_rectangle(0, 0, 0, 0, outline=ACCENT, width=3, fill="#2A5A46", stipple="gray25")
            state["size_bg"] = canvas.create_rectangle(0, 0, 0, 0, fill=PANEL, outline=ACCENT)
            state["size"] = canvas.create_text(0, 0, anchor="nw", fill=TEXT, font=(family, 12, "bold"), text="")
        state["box"] = (x1, y1, x2, y2)
        canvas.coords(state["rect"], x1, y1, x2, y2)
        canvas.itemconfigure(state["size"], text=f"{abs(x2 - x1)} × {abs(y2 - y1)}")
        canvas.coords(state["size"], max(x1, x2) - 96, max(y1, y2) + 12)
        bx1, by1, bx2, by2 = canvas.bbox(state["size"])
        canvas.coords(state["size_bg"], bx1 - 8, by1 - 4, bx2 + 8, by2 + 4)

    def save(_e=None):
        if "box" not in state:
            return
        bx1, bx2 = sorted((state["box"][0], state["box"][2]))
        by1, by2 = sorted((state["box"][1], state["box"][3]))
        region = {"left": left + bx1, "top": top + by1, "width": bx2 - bx1, "height": by2 - by1}
        out_path.write_text(json.dumps(region), encoding="utf-8")
        root.destroy()

    def press(e):
        state["x"], state["y"], state["drag"] = e.x, e.y, True
        show(e.x, e.y, e.x, e.y)

    def drag(e):
        if state.get("drag"):
            show(state["x"], state["y"], e.x, e.y)

    def release(e):
        if state.pop("drag", None):
            show(state["x"], state["y"], e.x, e.y)
            save()

    def key(dx, dy, grow):
        def go(_e):
            if state.get("drag"):
                return
            if "box" not in state:      # 第一下：先放一個框在主螢幕中間
                show(*first_box(root.winfo_screenwidth(), root.winfo_screenheight(), -min(left, 0), -min(top, 0)))
                return
            show(*nudge(state["box"], dx, dy, grow, canvas.winfo_width(), canvas.winfo_height()))
        return go

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    for name, (dx, dy) in {"Left": (-STEP, 0), "Right": (STEP, 0), "Up": (0, -STEP), "Down": (0, STEP)}.items():
        root.bind(f"<{name}>", key(dx, dy, False))
        root.bind(f"<Shift-{name}>", key(dx, dy, True))
    root.bind("<Return>", save)
    root.bind("<KP_Enter>", save)
    root.bind("<Escape>", lambda e: root.destroy())
    root.after(100, root.focus_force)
    root.mainloop()
