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


class _Slot:
    """一個區域目前讀到哪裡：上一張畫面、看過的句子、統計數字。"""

    def __init__(self, region: dict):
        self.region = region
        self.prev = self.last_done = self.moving_since = None
        self.first = True
        self.seen = SeenLines()
        # scans 辨識了幾次；lines 讀到幾行 = sent 拿去翻的 + repeat 讀過的 + other 不是要翻的語言 + old 一開始就在畫面上的
        self.stats = {"scans": 0, "lines": 0, "sent": 0, "repeat": 0, "other": 0, "old": 0}

    @staticmethod
    def key(region: dict) -> tuple:
        """位置、大小或跟著的程式變了才算換了一個區域；只改名字或開關不會從頭讀。"""
        return (region.get("id"), region.get("left"), region.get("top"), region.get("width"), region.get("height"),
                region.get("app") or "", tuple(region.get("anchor") or ()))


class ScreenWatcher(threading.Thread):
    """
    每隔一下子看一次螢幕上框選的區域，有新的一行就交給 on_message。可以同時看好幾個區域，各自記各自看過的句子。
    locate(區域) 回傳現在實際要擷取的位置（跟著程式視窗的區域會跟著視窗移動）；回傳 None 表示現在不讀（視窗沒開、縮小了）。
    """

    def __init__(self, ocr: OcrEngine, on_message, on_status=None, interval: float = 0.7,
                 source_langs: set[str] | None = None, first_scan_keep: int = 4, grab=None, locate=None, on_stats=None):
        super().__init__(daemon=True)
        self.ocr = ocr
        self.on_message = on_message          # on_message(原文, 語言代碼, 辨識分數 0~1, 區域)
        self.on_status = on_status or (lambda s: None)
        self.on_stats = on_stats or (lambda stats: None)      # on_stats({區域 id: 統計數字})，數字有變才呼叫
        self.interval = interval
        self.source_langs = source_langs or {"ko"}
        self.first_scan_keep = first_scan_keep
        self.grab = grab                      # 測試時可以換成別的擷取函式
        self.locate = locate or (lambda region: region)
        self.paused = False
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._slots: list[_Slot] = []
        self.failed = False

    @property
    def regions(self) -> list[dict]:
        with self._lock:
            return [slot.region for slot in self._slots]

    @property
    def region(self) -> dict | None:
        regions = self.regions
        return regions[0] if regions else None

    @property
    def seen(self) -> SeenLines:
        with self._lock:
            return self._slots[0].seen if self._slots else SeenLines()

    def set_regions(self, regions: list[dict]) -> None:
        """換成這幾個區域。沒變的區域接著讀（不會把畫面上的舊訊息再翻一次），新的和改過位置的從頭開始。"""
        with self._lock:
            old = {_Slot.key(slot.region): slot for slot in self._slots}
            slots = []
            for region in regions:
                slot = old.get(_Slot.key(region)) or _Slot(region)
                slot.region = region
                slots.append(slot)
            self._slots = slots

    def set_region(self, region: dict | None) -> None:
        """只看一個區域（每次呼叫都從頭開始讀）。"""
        with self._lock:
            self._slots = [_Slot(region)] if region else []

    def stats(self) -> dict:
        with self._lock:
            return {str(slot.region.get("id") or ""): dict(slot.stats) for slot in self._slots}

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
        told = None
        while not self._halt.is_set():
            time.sleep(self.interval)
            if self.paused:
                continue
            with self._lock:
                slots = list(self._slots)
            for slot in slots:
                if self._halt.is_set() or self.paused:
                    break
                try:
                    self._look(slot, grab)
                except Exception as ex:  # noqa: BLE001
                    self.on_status(str(ex))
                    time.sleep(2)
            now = self.stats()
            if now != told:
                told = now
                try:
                    self.on_stats(now)
                except Exception:  # noqa: BLE001
                    pass

    def _look(self, slot: _Slot, grab) -> None:
        place = self.locate(slot.region)
        if not place:
            slot.prev = None      # 視窗回來之後等畫面停下來再讀
            return
        bgr = np.ascontiguousarray(grab(place))
        th = self._thumb(bgr)
        stable = slot.prev is not None and float(np.abs(th - slot.prev).mean()) < 1.0
        # 只要有幾個小格子明顯變了就算有變化（一行新訊息只佔畫面很小一塊）
        changed = slot.last_done is None or int((np.abs(th - slot.last_done) > 16).sum()) >= 3
        slot.prev = th
        now = time.monotonic()
        # 等畫面停下來（捲動、動畫結束）再辨識，避免讀到一半的字；
        # 如果畫面一直在動（動態貼圖之類），最多等 3 秒就照樣辨識
        if not changed:
            slot.moving_since = None
            return
        if not stable:
            slot.moving_since = slot.moving_since or now
            if now - slot.moving_since < 3.0:
                return
        slot.moving_since = None
        slot.last_done = th
        lines = message_lines(self.ocr.read(bgr))
        slot.stats["scans"] += 1
        slot.stats["lines"] += len(lines)
        fresh = []
        for ln in lines:
            lang = wanted(ln.text, self.source_langs)
            if not lang:
                slot.stats["other"] += 1      # 不是要翻的語言（自己的語言、數字、符號）
            elif slot.seen.seen(ln.text):
                slot.stats["repeat"] += 1     # 之前讀過的同一行
            else:
                slot.seen.add(ln.text)
                fresh.append((ln.text, lang, ln.score))
        if slot.first:
            # 第一次只翻畫面最下面幾則，免得一口氣翻一整頁
            slot.first = False
            slot.stats["old"] += max(0, len(fresh) - self.first_scan_keep)
            fresh = fresh[-self.first_scan_keep:]
        slot.stats["sent"] += len(fresh)
        for text, lang, score in fresh:
            self.on_message(text, lang, score, slot.region)
