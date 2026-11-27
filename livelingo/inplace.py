"""
原地覆蓋翻譯（2.13.0）：把譯文直接蓋在畫面上原文的位置。選單、任務說明、道具描述、別人的聊天紀錄，
不用把眼睛移到別的地方看字幕。

做法：讀螢幕的功能每辨識一次畫面，會把讀到的一列一列字交過來；這裡接成一段一段、各翻一次（同一段字翻過就記住），
再畫一塊跟原文一樣大、底色取自原文旁邊的色塊，把譯文寫在上面。色塊放在一個透明、滑鼠穿得過去、
「不讓螢幕擷取拍到」的視窗裡：LiveLingo 自己讀畫面時看到的還是原文（不會把自己的譯文讀回來），
遊戲也完全沒被碰到。因為擷取拍不到，分享螢幕、截圖、錄影也看不到這些譯文，這是只給自己看的。
畫面變了（換頁、捲動、視窗移動）才會重新辨識，所以畫面沒動的時候不佔任何處理器。
"""

import threading
import time
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw

from . import chatpaint, snap
from .core.watcher import normalize

MAX_CACHE = 400        # 最多記住幾段翻好的
MAX_PENDING = 40       # 排隊等翻的最多幾段（再多就是整頁了，先翻畫面上方的）
CONTRAST = 90          # 字和底色的亮度差至少這麼多（0~255），不夠就改用黑或白
FILL_ALPHA = 252       # 色塊幾乎不透明；留一點點，邊緣看起來不會太硬


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def colors(bgr: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[tuple, tuple]:
    """這一塊原文的 (底色, 字色)，都是 RGB。底色 = 這一塊裡最多的顏色（字只佔少數）；
    字色 = 離底色最遠的那些點的平均；對比不夠（字和底色太像）就改黑或白。"""
    height, width = bgr.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(width, int(x + w)), min(height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0), (255, 255, 255)
    crop = bgr[y0:y1, x0:x1, :3].reshape(-1, 3).astype(np.int32)
    keys = (crop[:, 0] >> 4) * 256 + (crop[:, 1] >> 4) * 16 + (crop[:, 2] >> 4)
    mode = np.bincount(keys).argmax()
    near = crop[keys == mode]
    bg_bgr = near.mean(axis=0)
    dist = np.abs(crop - bg_bgr).sum(axis=1)
    far = crop[dist >= max(60, int(dist.max() * 0.6))] if dist.max() >= 60 else crop[:0]
    bg = (int(bg_bgr[2]), int(bg_bgr[1]), int(bg_bgr[0]))
    if len(far):
        m = far.mean(axis=0)
        fg = (int(m[2]), int(m[1]), int(m[0]))
    else:
        fg = bg
    if abs(_luma(fg) - _luma(bg)) < CONTRAST:
        fg = (20, 20, 20) if _luma(bg) > 140 else (245, 245, 245)
    return bg, fg


def paint(size: tuple[int, int], blocks: list[dict], han: str = "cjk", family: str = "sans") -> Image.Image:
    """畫出一張透明的圖（和被讀的區域一樣大），每一段有譯文的就在原文的位置畫一塊色塊和譯文。
    blocks：{"x","y","w","h","size","tr","bg","fg"}。字大概跟原文一樣大，放不下就縮小（最小 10 點）；
    縮到最小還放不下，色塊往下長到放得下為止。"""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for b in blocks:
        text = b.get("tr")
        if not text:
            continue
        pad = max(2, int(b["size"]) // 6)
        rx, ry, rw, rh = int(b["x"]) - pad, int(b["y"]) - pad, int(b["w"]) + 2 * pad, int(b["h"]) + 2 * pad
        fg = tuple(b["fg"])
        fs = max(10, round(int(b["size"]) * 0.92))
        while True:
            lines = chatpaint._wrap([(text, fg)], fs, max(20, rw - 2 * pad), han, family, False)
            line_h = round(fs * 1.25)
            total = len(lines) * line_h
            if total <= rh - pad or fs <= 10:
                break
            fs -= 1
        height = max(rh, total + pad)
        draw.rounded_rectangle((rx, ry, rx + rw, ry + height), radius=pad, fill=(*b["bg"], FILL_ALPHA))
        top = ry + max(pad // 2, (height - total) // 2)
        for n, row in enumerate(lines):
            x = float(rx + pad)
            for token, font, color, width in row:
                draw.text((x, top + n * line_h), token, font=font, fill=(*color, 255))
                x += width
    return image


class InPlace:
    """
    開著的時候，ScreenWatcher 每辨識一次畫面就呼叫 update()。translate(原文, 語言) 回傳譯文（空字串 = 翻不出來）；
    wanted(原文) 回傳要不要翻、原文是哪個語言（None = 不翻，例如已經是我的語言、數字、名字）。
    busy()：現在有別的翻譯在排隊（聊天、語音）：先讓它們，這邊等一下再翻。
    """

    def __init__(self, translate, wanted, window_class, target=lambda: "zh-TW", busy=lambda: False, log=lambda text: None):
        self.translate, self.wanted, self.window_class, self.target, self.busy, self.log = translate, wanted, window_class, target, busy, log
        self.on = False
        self._lock = threading.RLock()
        self._cache: OrderedDict = OrderedDict()
        self._pending: list[tuple] = []
        self._regions: dict[str, dict] = {}      # 區域 id → {"place", "size", "blocks"}
        self._windows: dict = {}
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- 開關

    def set_on(self, on: bool) -> None:
        with self._lock:
            self.on = bool(on)
            if self.on:
                self._stop.clear()
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._work, daemon=True, name="inplace")
                    self._thread.start()
            else:
                self._pending.clear()
                self._regions.clear()
                self._close_windows()

    def reset(self) -> None:
        """讀螢幕停了（來源關掉）：蓋在畫面上的譯文收起來。開關本身不變，再開讀螢幕就會回來。"""
        with self._lock:
            self._pending.clear()
            ids = list(self._regions)
            self._regions.clear()
        for rid in ids:
            self._redraw(rid)

    def keep_only(self, ids: set) -> None:
        """區域的清單改了：不在清單裡的區域，譯文收起來。"""
        with self._lock:
            gone = [rid for rid in self._regions if rid not in ids]
            for rid in gone:
                self._regions.pop(rid, None)
        for rid in gone:
            self._redraw(rid)

    def clear_cache(self) -> None:
        """換了語言、語氣：舊的譯文不能再用。"""
        with self._lock:
            self._cache.clear()
            self._pending.clear()
            for state in self._regions.values():
                for b in state["blocks"]:
                    b["tr"] = None
            self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            self.on = False
            self._close_windows()

    def _close_windows(self) -> None:
        for window in self._windows.values():
            try:
                window.close()
            except Exception:  # noqa: BLE001
                pass
        self._windows.clear()

    # ---- 讀螢幕那邊交過來的畫面

    def update(self, region: dict, place: dict | None, bgr, lines: list) -> bool:
        """一個區域剛辨識完的結果。回傳 True = 這個區域交給我處理（開著的時候就是）。place 是 None：區域現在不讀（視窗縮小了）。"""
        if not self.on:
            return False
        rid = str(region.get("id") or "")
        if place is None or bgr is None:
            with self._lock:
                self._regions.pop(rid, None)
            self._redraw(rid)
            return True
        target = self.target()
        blocks = []
        for b in snap.blocks(lines):
            lang = self.wanted(b["text"])
            if not lang:
                continue
            key = (normalize(b["text"]), target)
            bg, fg = colors(bgr, b["x"] - 2, b["y"] - 2, b["w"] + 4, b["h"] + 4)
            with self._lock:
                tr = self._cache.get(key)
                if tr is not None:
                    self._cache.move_to_end(key)
                elif not any(p[0] == key for p in self._pending) and len(self._pending) < MAX_PENDING:
                    self._pending.append((key, b["text"], lang))
            blocks.append(dict(b, key=key, lang=lang, tr=tr, bg=bg, fg=fg))
        with self._lock:
            self._regions[rid] = {"place": dict(place), "size": (bgr.shape[1], bgr.shape[0]), "blocks": blocks, "region": dict(region)}
        self._wake.set()
        self._redraw(rid)
        return True

    # ---- 翻譯（背景一條線，由上到下一段一段翻）

    def _work(self) -> None:
        while not self._stop.is_set():
            item = None
            with self._lock:
                if self._pending and self.on:
                    item = self._pending[0]
            if item is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            if self.busy():
                time.sleep(0.4)      # 聊天、語音的翻譯先
                continue
            key, text, lang = item
            try:
                done = (self.translate(text, lang) or "").strip()
            except Exception as ex:  # noqa: BLE001
                self.log(f"in-place translation failed: {ex!r}")
                done = ""
            with self._lock:
                if self._pending and self._pending[0][0] == key:
                    self._pending.pop(0)
                if done:
                    self._cache[key] = done
                    while len(self._cache) > MAX_CACHE:
                        self._cache.popitem(last=False)
                    ids = []
                    for rid, state in self._regions.items():
                        hit = False
                        for b in state["blocks"]:
                            if b["key"] == key:
                                b["tr"], hit = done, True
                        if hit:
                            ids.append(rid)
                else:
                    ids = []
            for rid in ids:
                self._redraw(rid)

    # ---- 畫

    def _redraw(self, rid: str) -> None:
        with self._lock:
            state = self._regions.get(rid)
            window = self._windows.get(rid)
            if state is None:
                if window is not None:
                    try:
                        window.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._windows.pop(rid, None)
                return
            shown = [dict(b) for b in state["blocks"] if b.get("tr")]
            if not shown and window is None:
                return
            place, size = state["place"], state["size"]
            if window is None:
                window = self.window_class()
                window.hidden = True      # 擷取拍不到：讀螢幕時看到的一直是原文
                rect = [place["left"], place["top"], size[0], size[1]]
                if not window.start(rect):
                    self.log(f"in-place window failed: {getattr(window, 'error', '')}")
                    return
                self._windows[rid] = window
        image = paint(size, shown, han=chatpaint.han_kind(self.target()))
        window.paint(image, [place["left"], place["top"], size[0], size[1]])

    def shown(self) -> int:
        """現在蓋在畫面上的譯文有幾段（測試和介面用）。"""
        with self._lock:
            return sum(1 for s in self._regions.values() for b in s["blocks"] if b.get("tr"))
