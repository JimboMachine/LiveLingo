"""盯著螢幕上框選的聊天區，畫面有變化就辨識文字，把「沒看過的新訊息」交出去翻譯。"""

import difflib
import re
import threading
import time
from collections import deque

import cv2
import numpy as np

from .ocr import Line, OcrEngine, has_hangul, message_lines
from .translate import LATIN, detect_language

_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize(s: str) -> str:
    return _NORM_RE.sub("", s).lower()


def alike(a: str, b: str) -> bool:
    """兩行（已經去掉標點和空白的）是不是同一行。辨識結果每次會差一兩個字，所以用「很像」來比。"""
    if a == b:
        return True
    return abs(len(a) - len(b)) <= max(2, len(b) // 4) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


class SeenLines:
    """記住看過的訊息。辨識結果每次會差一兩個字，所以用「相似」而不是「完全一樣」來比。"""

    def __init__(self, size: int = 300):
        self.items: deque[str] = deque(maxlen=size)
        self.parts: deque[str] = deque(maxlen=size * 2)      # 換行接起來的訊息裡的每一段（上半捲出畫面之後，剩下的幾行還認得）

    def match(self, text: str) -> str:
        """same：讀過同一則；inside：這一列包著讀過的句子（回覆預覽列是「名字 + 舊訊息」）；
        part：是某一則換行訊息裡的幾行；空字串：沒看過。"""
        n = normalize(text)
        if not n:
            return "same"
        if any(alike(old, n) for old in self.items):
            return "same"
        if any(len(old) >= 5 and old in n for old in self.items):
            return "inside"
        if any(alike(old, n) for old in self.parts):
            return "part"
        return ""

    def seen(self, text: str) -> bool:
        return bool(self.match(text))

    def add(self, text: str, parts: list[str] | None = None):
        n = normalize(text)
        if n:
            self.items.append(n)
        rows = [normalize(part) for part in parts or []]
        if len(rows) > 1:
            for start in range(len(rows)):
                for end in range(start + 1, len(rows) + 1):
                    piece = "".join(rows[start:end])
                    if piece and piece != n:
                        self.parts.append(piece)


def new_from(old: list[str], new: list[str]) -> int | None:
    """
    聊天是往上捲的：這一次讀到的前幾行，就是上一次讀到的最後幾行。回傳「這一次從第幾行開始是上次沒有的」。
    對不起來（上次沒讀到東西、一口氣捲了大半頁、聊天框收起來又打開）回傳 None，這時候只能照內容判斷。
    要對得上至少一半的行才算數，免得剛好一行一樣就當成是接著的。
    """
    if not old or not new:
        return None
    most = min(len(old), len(new))
    for size in range(most, max(1, (most + 1) // 2) - 1, -1):
        if all(alike(a, b) for a, b in zip(old[-size:], new[:size])):
            return size
    return None


def _wide(ch: str) -> bool:
    return ord(ch) > 0x2E7F


def _first_word_width(row: Line) -> float:
    """這一列第一個詞大概多寬。中文、日文沒有空白，可以在任何一個字後面換行，所以算一個字。"""
    text = row.text.strip()
    if not text or not row.w:
        return 0.0
    word = text.split()[0]
    if not has_hangul(word) and any(_wide(ch) for ch in word):
        word = word[0]
    cells = sum(2 if _wide(ch) else 1 for ch in text)
    return row.w * sum(2 if _wide(ch) else 1 for ch in word) / max(1, cells)


def _continues(a: Line, b: Line, edge: float) -> bool:
    """b 是不是 a 換行下來的：對齊同一個左邊、行距正常，而且 b 的第一個詞本來就放不進 a 那一行剩下的空間。"""
    if not a.w or not b.w or edge <= 0:
        return False
    tall = max(a.h, b.h)
    if abs(a.x - b.x) > 0.5 * tall or not 0.7 <= a.h / b.h <= 1.4 or not 0 < b.y - a.y <= 1.9 * tall:
        return False
    return a.x + a.w + 0.35 * a.h + _first_word_width(b) > edge


def _glue(a: str, b: str) -> str:
    """接兩行：中文、日文中間不加空白，其他的加一個。"""
    solid = a and b and _wide(a[-1]) and _wide(b[0]) and not has_hangul(a[-1] + b[0])
    return a + b if solid else f"{a} {b}"


def join_wrapped(rows: list[Line], seen: SeenLines, edge: float, most: int = 4) -> list[tuple[Line, list[str]]]:
    """
    一則訊息太長、在聊天視窗裡換成兩三行的，接回一則再翻（一行一行分開翻，句子會從中間斷掉）。
    edge：這個區域裡文字最右邊到哪裡（0 = 還不知道，不接）。以前單獨讀到過的那一行是一則獨立的訊息，不拿來接。
    回傳 [(整則, [每一行的字])]；沒有換行的那幾則，後面那個清單只有一項。
    """
    out: list[tuple[Line, list[str]]] = []
    i = 0
    while i < len(rows):
        group = [rows[i]]
        while (len(group) < most and i + len(group) < len(rows) and _continues(group[-1], rows[i + len(group)], edge)
               and seen.match(group[-1].text) != "same" and seen.match(rows[i + len(group)].text) != "same"):
            group.append(rows[i + len(group)])
        if len(group) == 1:
            out.append((group[0], [group[0].text]))
        else:
            text = group[0].text
            for row in group[1:]:
                text = _glue(text, row.text)
            first = group[0]
            out.append((Line(text, first.x, first.y, first.h, min(r.score for r in group), max(r.w for r in group)),
                        [r.text for r in group]))
        i += len(group)
    return out


def text_edge(rows: list[Line], width: int, known: float = 0.0) -> tuple[float, float]:
    """文字最右邊到哪裡：回傳（到目前為止看過最右邊的位置, 拿來判斷換行用的位置）。
    看過的最長一行還不到區域的六成寬時，還不能當成「寫到最右邊」，先不判斷換行（回傳 0）。"""
    known = max([known] + [float(r.x + r.w) for r in rows if r.w])
    return known, (known if known >= 0.6 * width else 0.0)


def wanted(text: str, source_langs: set[str]) -> str | None:
    """這一列要不要翻？回傳語言代碼（zh / ko / ja / en / es …），不翻就回傳 None。"""
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
    if lang in LATIN:
        if len(re.findall(r"[^\W\d_]{2,}", text)) < 3:      # 太短的一列（名字、按鈕）不翻
            return None
        if lang in source_langs:
            return lang
        # 短句常常看不出是哪一種拉丁字母的語言：對方用的是拉丁字母的語言，就當成那一種
        latin = [l for l in source_langs if l in LATIN and l != "en"]
        if len(latin) == 1:
            return latin[0]
        return "en" if "en" in source_langs and not latin else None
    if lang in ("th", "ru") and lang in source_langs:
        return lang
    return None


BUSY_MOVE = 18.0       # 縮圖平均變動超過這麼多：整塊畫面都在動（戰鬥、轉鏡頭）
BUSY_AFTER = 3.0       # 連續這麼多秒都這樣才算戰鬥中
BUSY_SLOW = 3          # 戰鬥中每隔幾倍的時間才讀一次
STORY_WAIT = 12.0      # 劇情對話框：畫面一直在動時最多等幾秒（一般區域是 3 秒）


# 聊天頻道分色（2.16.0）：公會、隊伍、全體聊天在畫面上顏色不一樣
def line_color(bgr, x: int, y: int, w: int, h: int) -> str:
    """這一列字的顏色（#RRGGBB）。拿這一塊裡跟背景差最多的那些點（字）的平均色；看不出來就是空字串。"""
    if w <= 2 or h <= 2:
        return ""
    box = bgr[max(0, y):max(0, y) + h, max(0, x):max(0, x) + w]
    if box.size == 0:
        return ""
    px = box.reshape(-1, 3).astype(np.float32)
    bg = np.median(px, axis=0)
    far = np.abs(px - bg).sum(axis=1)
    keep = px[far >= max(60.0, np.percentile(far, 85))]
    if len(keep) < 6:
        return ""
    b, g, r = (int(v) for v in keep.mean(axis=0))
    return f"#{r:02X}{g:02X}{b:02X}"


class _Slot:
    """一個區域目前讀到哪裡：上一張畫面、看過的句子、統計數字。"""

    def __init__(self, region: dict):
        self.region = region
        self.prev = self.last_done = self.moving_since = None
        self.first = True
        self.seen = SeenLines()
        self.last: list[str] = []      # 上一次讀到的每一則（由上到下），用來看出哪幾則是新出現在下面的
        self.edge = 0.0                # 這個區域裡看過的文字最右邊到哪裡（判斷換行用）
        self.dark_since = None         # 從什麼時候開始抓到的畫面是全黑的
        self.black = False             # 已經黑了好幾秒：多半是遊戲用「全螢幕」，Windows 不讓別的程式看到畫面
        self.story_text = None         # 劇情對話框（2.14.0）：上一次讀到的字；跟這次一樣才算打完了
        # scans 辨識了幾次；lines 讀到幾行 = sent 拿去翻的 + repeat 讀過的 + other 不是要翻的語言 + old 一開始就在畫面上的
        self.stats = {"scans": 0, "lines": 0, "sent": 0, "repeat": 0, "other": 0, "old": 0}

    @staticmethod
    def key(region: dict) -> tuple:
        """位置、大小或跟著的程式變了才算換了一個區域；只改名字或開關不會從頭讀。"""
        return (region.get("id"), region.get("left"), region.get("top"), region.get("width"), region.get("height"),
                region.get("app") or "", tuple(region.get("anchor") or ()))


def blank_covers(bgr: np.ndarray, place: dict, covers: list) -> np.ndarray:
    """把 covers（螢幕上的 [左, 上, 寬, 高]）和這次擷取的位置 place 重疊的部分塗成黑色。回傳新的圖，原本的不動。"""
    out = None
    h, w = bgr.shape[:2]
    for left, top, width, height in covers:
        x0, y0 = max(0, int(left) - int(place["left"])), max(0, int(top) - int(place["top"]))
        x1, y1 = min(w, int(left) + int(width) - int(place["left"])), min(h, int(top) + int(height) - int(place["top"]))
        if x1 > x0 and y1 > y0:
            if out is None:
                out = bgr.copy()
            out[y0:y1, x0:x1] = 0
    return bgr if out is None else out


class ScreenWatcher(threading.Thread):
    """
    每隔一下子看一次螢幕上框選的區域，有新的一行就交給 on_message。可以同時看好幾個區域，各自記各自看過的句子。
    locate(區域) 回傳現在實際要擷取的位置（跟著程式視窗的區域會跟著視窗移動）；回傳 None 表示現在不讀（視窗沒開、縮小了）。
    """

    def __init__(self, ocr: OcrEngine, on_message, on_status=None, interval: float = 0.7,
                 source_langs: set[str] | None = None, first_scan_keep: int = 4, grab=None, locate=None, on_stats=None,
                 on_black=None, mask=None):
        super().__init__(daemon=True)
        # mask()：現在有哪些東西蓋在螢幕上（LiveLingo 自己的字幕和視窗）[[左, 上, 寬, 高], ...]。擷取時先塗黑，才不會把自己顯示的字又讀進來翻一次
        self.mask = mask or (lambda: [])
        self.ocr = ocr
        self.on_message = on_message          # on_message(原文, 語言代碼, 辨識分數 0~1, 區域)
        self.on_status = on_status or (lambda s: None)
        self.on_stats = on_stats or (lambda stats: None)      # on_stats({區域 id: 統計數字})，數字有變才呼叫
        self.on_black = on_black or (lambda ids: None)        # on_black([區域 id])：哪幾個區域抓到的畫面是全黑的，有變才呼叫
        # on_frame(區域, 現在的位置, 畫面, 辨識出的每一列)：交給原地覆蓋翻譯。回傳 True = 它接手了這一區，不再送去浮動留言
        self.on_frame = None
        self.black_after = 5.0                                # 連續黑幾秒才算（切換畫面時黑一下不算）
        self.interval = interval
        self.source_langs = source_langs or {"ko"}
        # 雙語字幕：有設的話（我的語言那一家，例如 "zh"），要翻的那一行緊鄰著一行這種語言的字，就當成畫面上現成的翻譯一起交出去
        self.pair_lang: str | None = None
        self.first_scan_keep = first_scan_keep
        self.grab = grab                      # 測試時可以換成別的擷取函式
        self.locate = locate or (lambda region: region)
        self.paused = False
        # 聊天頻道分色（2.16.0）：True 的時候每一則多交一個 color=（那一列字的顏色），on_message 要收得下
        self.colors = False
        # 戰鬥中降低讀取（2.16.0）：畫面一直大幅變動（打架、轉鏡頭）時，等久一點再讀，不跟遊戲搶電腦
        self.slow_busy = False
        self._busy_since: float | None = None
        self._busy_seen = 0.0
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

    def black(self) -> list[str]:
        """現在抓到的畫面是全黑的區域。"""
        with self._lock:
            return [str(slot.region.get("id") or "") for slot in self._slots if slot.black]

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

    def busy(self) -> bool:
        """畫面最近一直在大幅變動（連續 BUSY_AFTER 秒）：戰鬥中，讀慢一點。"""
        return bool(self.slow_busy and self._busy_since is not None and time.monotonic() - self._busy_since >= BUSY_AFTER
                    and time.monotonic() - self._busy_seen < 2.0)

    def _loop(self, grab):
        told = None
        while not self._halt.is_set():
            time.sleep(self.interval * (BUSY_SLOW if self.busy() else 1))
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
            if self.on_frame:
                self.on_frame(slot.region, None, None, [])      # 視窗不見了：蓋在上面的譯文也收起來
            return
        bgr = np.ascontiguousarray(grab(place))
        try:
            covers = self.mask()
        except Exception:  # noqa: BLE001
            covers = []
        if covers:
            bgr = blank_covers(bgr, place, covers)
        # 整塊全黑：遊戲用「全螢幕」（不是無邊框）時，抓到的畫面就是這樣。黑了幾秒就告訴使用者，不然只會看到「什麼都沒翻」
        dark = bool(bgr.size) and int(bgr.max()) < 12
        slot.dark_since = (slot.dark_since or time.monotonic()) if dark else None
        black = dark and time.monotonic() - slot.dark_since >= self.black_after
        if black != slot.black:
            slot.black = black
            try:
                self.on_black(self.black())
            except Exception:  # noqa: BLE001
                pass
        th = self._thumb(bgr)
        moved = float(np.abs(th - slot.prev).mean()) if slot.prev is not None else 0.0
        stable = slot.prev is not None and moved < 1.0
        if moved >= BUSY_MOVE:      # 整塊畫面都在變（不是多一行字）：記下來，一直這樣就讀慢一點
            self._busy_seen = time.monotonic()
            self._busy_since = self._busy_since or self._busy_seen
        elif self._busy_since is not None and time.monotonic() - self._busy_seen > 2.0:
            self._busy_since = None
        # 只要有幾個小格子明顯變了就算有變化（一行新訊息只佔畫面很小一塊）
        changed = slot.last_done is None or int((np.abs(th - slot.last_done) > 16).sum()) >= 3
        slot.prev = th
        now = time.monotonic()
        # 等畫面停下來（捲動、動畫結束）再辨識，避免讀到一半的字；
        # 如果畫面一直在動（動態貼圖之類），最多等 3 秒就照樣辨識。
        # 劇情對話框（區域設成 story，2.14.0）：字是一個一個跳出來的，最多等 STORY_WAIT 秒，
        # 而且要連續兩次讀到一樣的字才算打完（不然會翻到半句）
        story = bool(slot.region.get("story"))
        waiting = story and slot.story_text is not None
        if not changed and not waiting:
            slot.moving_since = None
            return
        if not stable:
            slot.moving_since = slot.moving_since or now
            if now - slot.moving_since < (STORY_WAIT if story else 3.0):
                return
        slot.moving_since = None
        slot.last_done = th
        raw = self.ocr.read(bgr)
        if story:
            text = " ".join(normalize(str(getattr(r, "text", r))) for r in raw)
            if text and text != slot.story_text:
                slot.story_text = text      # 字還在變（或剛出現）：下一輪再讀一次，一樣才翻
                return
            slot.story_text = None
        if self.on_frame and self.on_frame(slot.region, place, bgr, raw):
            slot.stats["scans"] += 1      # 譯文直接蓋在原處，不進浮動留言
            return
        rows = message_lines(raw)
        slot.edge, edge = text_edge(rows, bgr.shape[1], slot.edge)
        lines = join_wrapped(rows, slot.seen, edge)      # 換行的訊息接回一則
        texts = [normalize(ln.text) for ln, _ in lines]
        start = new_from(slot.last, texts)               # 從第幾則開始是上一次沒有的（對不起來是 None）
        slot.last = texts
        slot.stats["scans"] += 1
        slot.stats["lines"] += len(lines)
        fresh = []
        pairs = self._pairs(lines)                       # 雙語字幕：要翻的第 i 行 → 旁邊那一行（我的語言）
        shown_rows = set(pairs.values())
        for i, (ln, parts) in enumerate(lines):
            if i in shown_rows:
                slot.stats["other"] += 1      # 雙語字幕裡我的語言那一行：是翻譯，不是要翻的
                continue
            lang = wanted(ln.text, self.source_langs)
            if not lang:
                slot.stats["other"] += 1      # 不是要翻的語言（自己的語言、數字、符號）
                continue
            how = slot.seen.match(ln.text)
            # 有人又說了一次一樣的話（「gg」「ㅋㅋㅋ」）：內容讀過，但它是新出現在最下面的，要算新的一則
            again = how == "same" and start is not None and i >= start
            if how and not again:
                slot.stats["repeat"] += 1     # 之前讀過的同一則
            else:
                slot.seen.add(ln.text, parts)
                color = line_color(bgr, ln.x, ln.y, getattr(ln, "w", 0) or len(ln.text) * ln.h, ln.h) if self.colors else ""
                fresh.append((ln.text, lang, ln.score, lines[pairs[i]][0].text if i in pairs else "", color))
        if slot.first:
            # 第一次只翻畫面最下面幾則，免得一口氣翻一整頁
            slot.first = False
            slot.stats["old"] += max(0, len(fresh) - self.first_scan_keep)
            fresh = fresh[-self.first_scan_keep:]
        slot.stats["sent"] += len(fresh)
        for text, lang, score, shown, color in fresh:
            more = {"color": color} if self.colors else {}
            if shown:
                self.on_message(text, lang, score, slot.region, shown, **more)
            else:
                self.on_message(text, lang, score, slot.region, **more)

    def _pairs(self, lines) -> dict[int, int]:
        """雙語字幕：{要翻的那一行: 緊鄰的我的語言那一行}。沒開（pair_lang 是 None）就是空的。
        先看下面一行（大部分雙語字幕原文在上），再看上面一行；一行翻譯只配一次。
        日文和中文都有漢字，所以「我的語言那一行」要用 detect_language 認（沒有假名的才是中文），不能用 wanted。"""
        if not self.pair_lang:
            return {}
        from .translate import detect_language

        langs = [detect_language(ln.text) for ln, _ in lines]
        out, used = {}, set()
        for i, lang in enumerate(langs):
            if lang == self.pair_lang or not wanted(lines[i][0].text, self.source_langs):
                continue
            for j in (i + 1, i - 1):
                if 0 <= j < len(lines) and j not in used and j not in out and langs[j] == self.pair_lang:
                    out[i] = j
                    used.add(j)
                    break
        return out
