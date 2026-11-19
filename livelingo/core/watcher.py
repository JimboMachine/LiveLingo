"""盯著螢幕上框選的聊天區，畫面有變化就辨識文字，把「沒看過的新訊息」交出去翻譯。"""

import difflib
import re
import threading
import time
from collections import deque

import cv2
import numpy as np

from .ocr import OcrEngine, has_hangul, message_lines
from .translate import detect_language

_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize(s: str) -> str:
    return _NORM_RE.sub("", s).lower()


class SeenLines:
    """記住看過的訊息。辨識結果每次會差一兩個字，所以用「相似」而不是「完全一樣」來比。"""

    def __init__(self, size: int = 300):
        self.items: deque[str] = deque(maxlen=size)

    def seen(self, text: str) -> bool:
        n = normalize(text)
        if not n:
            return True
        for old in self.items:
            if n == old:
                return True
            # 回覆預覽列會是「名字 + 舊訊息」，包含看過的句子就當作看過
            if len(old) >= 5 and old in n:
                return True
            if abs(len(old) - len(n)) <= max(2, len(n) // 4) and difflib.SequenceMatcher(None, old, n).ratio() >= 0.82:
                return True
        return False

    def add(self, text: str):
        n = normalize(text)
        if n:
            self.items.append(n)


def wanted(text: str, source_langs: set[str]) -> str | None:
    """這一列要不要翻？回傳語言代碼（zh / ko / ja / en），不翻就回傳 None。"""
    if "ko" in source_langs and has_hangul(text):
        return "ko"
    lang = detect_language(text)
    if lang == "ja" and "ja" in source_langs:
        return "ja"
    if lang == "zh":
        # 只有漢字的一列：對方用日文時也算日文（日文句子可能剛好沒有假名）
        if len(re.findall(r"[\u4e00-\u9fff]", text)) < 2:
            return None
        if "zh" in source_langs:
            return "zh"
        if "ja" in source_langs:
            return "ja"
    if lang == "en" and "en" in source_langs:
        if len(re.findall(r"[A-Za-z]{2,}", text)) >= 3:
            return "en"
    return None


class ScreenWatcher(threading.Thread):
    def __init__(self, ocr: OcrEngine, on_message, on_status=None, interval: float = 0.7,
                 source_langs: set[str] | None = None, first_scan_keep: int = 4, grab=None):
        super().__init__(daemon=True)
        self.ocr = ocr
        self.on_message = on_message          # on_message(原文, 語言代碼)
        self.on_status = on_status or (lambda s: None)
        self.interval = interval
        self.source_langs = source_langs or {"ko"}
        self.first_scan_keep = first_scan_keep
        self.grab = grab                      # 測試時可以換成別的擷取函式
        self.region: dict | None = None       # {"left","top","width","height"}
        self.paused = False
        self._halt = threading.Event()
        self._reset = True
        self.failed = False
        self.seen = SeenLines()

    def set_region(self, region: dict | None):
        self.region = region
        self._reset = True

    def stop(self):
        self._halt.set()

    @staticmethod
    def _thumb(bgr: np.ndarray) -> np.ndarray:
        return cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (96, 96), interpolation=cv2.INTER_AREA).astype(np.int16)

    def run(self):
        try:
            if self.grab:
                self._loop(self.grab)
            else:
                import mss

                with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                    self._loop(lambda region: np.asarray(sct.grab(region))[:, :, :3])
        except Exception as ex:  # noqa: BLE001
            self.failed = True       # 整個讀螢幕的功能停了（例如抓不到螢幕畫面）
            self.on_status(str(ex))

    def _loop(self, grab):
        prev = last_done = moving_since = None
        first = True
        while not self._halt.is_set():
            time.sleep(self.interval)
            region = self.region
            if not region or self.paused:
                continue
            if self._reset:
                self._reset = False
                prev = last_done = None
                first = True
                self.seen = SeenLines()
            try:
                bgr = np.ascontiguousarray(grab(region))
                th = self._thumb(bgr)
                stable = prev is not None and float(np.abs(th - prev).mean()) < 1.0
                # 只要有幾個小格子明顯變了就算有變化（一行新訊息只佔畫面很小一塊）
                changed = last_done is None or int((np.abs(th - last_done) > 16).sum()) >= 3
                prev = th
                now = time.monotonic()
                # 等畫面停下來（捲動、動畫結束）再辨識，避免讀到一半的字；
                # 如果畫面一直在動（動態貼圖之類），最多等 3 秒就照樣辨識
                if not changed:
                    moving_since = None
                    continue
                if not stable:
                    moving_since = moving_since or now
                    if now - moving_since < 3.0:
                        continue
                moving_since = None
                last_done = th
                lines = message_lines(self.ocr.read(bgr))
                fresh = []
                for ln in lines:
                    lang = wanted(ln.text, self.source_langs)
                    if lang and not self.seen.seen(ln.text):
                        self.seen.add(ln.text)
                        fresh.append((ln.text, lang))
                if first:
                    # 第一次只翻畫面最下面幾則，免得一口氣翻一整頁
                    first = False
                    fresh = fresh[-self.first_scan_keep:]
                for text, lang in fresh:
                    self.on_message(text, lang)
            except Exception as ex:  # noqa: BLE001
                self.on_status(str(ex))
                time.sleep(2)
