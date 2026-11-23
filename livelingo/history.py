"""
對話歷史：翻好的句子存在這台電腦上（history.jsonl，一則一行），重開程式還看得到。可以搜尋、匯出成文字檔、一鍵清除，
也可以整個關掉不保存。不會上傳到任何地方。

檔案只往後面加：同一則訊息改過（重翻、改原文）就再加一行，讀的時候以最後一行為準；被拿掉的訊息加一行記號。
開程式時把過期的、重複的行整理掉。
"""

import json
import threading
import time
from pathlib import Path

from .chatpaint import NAMES
from .transcript import COLON

TITLES = {
    "zh-TW": "對話歷史", "zh-CN": "对话历史", "en": "Conversation history", "ko": "대화 기록", "ja": "会話の履歴",
    "vi": "Lịch sử hội thoại", "th": "ประวัติการสนทนา", "id": "Riwayat percakapan", "es": "Historial de conversación", "pt": "Histórico de conversa", "ru": "История разговора", "de": "Gesprächsverlauf", "fr": "Historique des conversations",
}
FILE = "history.jsonl"
MAX_ITEMS = 20000                  # 最多留這麼多則，再多就丟掉最舊的
DAYS = (7, 30, 90, 0)              # 可以選保留幾天；0 = 不限
FIELDS = ("who", "via", "original", "lang", "translated", "target", "place")


def render(items: list[dict], ui_lang: str) -> str:
    """把歷史排成文字（舊的在上面）。每天一個標題；每則一段：時間、誰說的、原文，下一行是翻譯。"""
    names = NAMES.get(ui_lang) or NAMES["en"]
    colon = COLON.get(ui_lang, ": ")
    lines = [f"LiveLingo {TITLES.get(ui_lang) or TITLES['en']}", ""]
    day = ""
    for it in items:
        at = time.localtime(it["at"])
        today = time.strftime("%Y-%m-%d", at)
        if today != day:
            day = today
            lines += [f"── {day} ──", ""]
        who = names["me"] if it["who"] == "me" else names["screen" if it["via"] == "screen" else "heard"]
        original, translated = it["original"].strip(), (it.get("translated") or "").strip()
        lines.append(f"[{time.strftime('%H:%M:%S', at)}] {who}{colon}{original}")
        if translated and translated != original:
            lines.append(f"→ {translated}")
        lines.append("")
    return "\r\n".join(lines)


class History:
    def __init__(self, folder: Path, keep: bool = True, days: int = 30, now=time.time):
        self.path = folder / FILE
        self.keep, self.days = bool(keep), int(days)
        self._now = now
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}      # 鍵 → 內容，照第一次出現的順序
        self._session = time.strftime("%Y%m%d%H%M%S", time.localtime(now()))
        self.error = ""
        self._load()

    # ---- 讀檔、整理

    def _load(self) -> None:
        lines = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    lines += 1
                    try:
                        row = json.loads(line)
                        key = str(row["k"])
                        if row.get("x"):
                            self._items.pop(key, None)
                        elif key in self._items:
                            self._items[key].update({k: row[k] for k in FIELDS if k in row})
                        else:
                            self._items[key] = {"k": key, "at": float(row["at"]), **{k: row.get(k, "") for k in FIELDS}}
                    except (ValueError, KeyError, TypeError):
                        continue      # 壞掉的一行（例如寫到一半斷電）：跳過，其他的照讀
        except FileNotFoundError:
            return
        except OSError as ex:
            self.error = str(ex)
            return
        before = len(self._items)
        self._trim()
        if lines != len(self._items) or before != len(self._items):
            self._rewrite()

    def _trim(self) -> None:
        if self.days > 0:
            oldest = self._now() - self.days * 86400
            self._items = {k: v for k, v in self._items.items() if v["at"] >= oldest}
        if len(self._items) > MAX_ITEMS:
            self._items = dict(list(self._items.items())[-MAX_ITEMS:])

    def _rewrite(self) -> bool:
        try:
            if not self._items:
                self.path.unlink(missing_ok=True)
                return True
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for row in self._items.values():
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            tmp.replace(self.path)
            self.error = ""
            return True
        except OSError as ex:
            self.error = str(ex)
            return False

    def _append(self, row: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.error = ""
        except OSError as ex:
            self.error = str(ex)

    # ---- 記錄

    def key_of(self, msg_id) -> str:
        return f"{self._session}-{msg_id}"

    def add(self, msg: dict) -> bool:
        """翻好的訊息記下來（還在翻的、翻失敗的不記）。同一則再來一次就更新內容。回傳有沒有記。"""
        if not self.keep or msg.get("pending") or msg.get("error") or not msg.get("translated"):
            return False
        key = self.key_of(msg["id"])
        with self._lock:
            old = self._items.get(key)
            row = {"k": key, "at": old["at"] if old else float(msg.get("at") or self._now()),
                   **{k: str(msg.get(k) or "") for k in FIELDS}}
            if old == row:
                return False
            self._items[key] = row
            if len(self._items) > MAX_ITEMS + 500:
                self._trim()
                self._rewrite()
            else:
                self._append(row)
        return True

    def drop(self, msg_id) -> None:
        key = self.key_of(msg_id)
        with self._lock:
            if self._items.pop(key, None) is not None:
                self._append({"k": key, "x": 1})

    def configure(self, keep: bool, days: int) -> None:
        """改了「要不要保存」「保留幾天」。縮短天數會馬上把過期的刪掉；關掉保存只是不再記新的，已經存的要按清除。"""
        with self._lock:
            self.keep, self.days = bool(keep), int(days)
            before = len(self._items)
            self._trim()
            if len(self._items) != before:
                self._rewrite()

    # ---- 看、搜尋、匯出、清除

    @property
    def count(self) -> int:
        return len(self._items)

    def _matching(self, q: str = "") -> list[dict]:
        words = [w for w in str(q or "").lower().split() if w]
        with self._lock:
            rows = list(self._items.values())
        if words:      # 每個詞都要出現（在原文或翻譯裡），不分大小寫
            rows = [r for r in rows if all(w in (r["original"] + "\n" + r["translated"]).lower() for w in words)]
        return rows

    def search(self, q: str = "", offset: int = 0, limit: int = 50) -> dict:
        """最新的在最前面。回傳 {"items", "total"（符合的有幾則）, "all"（總共幾則）, "more"（後面還有沒有）}。"""
        rows = self._matching(q)
        rows.reverse()
        offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
        page = rows[offset:offset + limit]
        return {"items": [dict(r) for r in page], "total": len(rows), "all": self.count, "more": offset + limit < len(rows)}

    def export(self, folder: Path, ui_lang: str, q: str = "") -> tuple[Path | None, int]:
        """把（符合搜尋的）歷史存成一個文字檔。沒有東西可以存就回傳 (None, 0)。存不進去會丟出 OSError。"""
        rows = self._matching(q)
        if not rows:
            return None, 0
        folder.mkdir(parents=True, exist_ok=True)
        stem = "LiveLingo history " + time.strftime("%Y-%m-%d %H.%M", time.localtime(self._now()))
        path, n = folder / f"{stem}.txt", 2
        while path.exists():
            path, n = folder / f"{stem} ({n}).txt", n + 1
        path.write_text(render(rows, ui_lang), encoding="utf-8-sig", newline="")      # 加 BOM，舊版記事本才不會亂碼
        return path, len(rows)

    def clear(self) -> int:
        """全部清除：記憶體裡的和檔案都刪掉。回傳刪了幾則。"""
        with self._lock:
            n = len(self._items)
            self._items = {}
            try:
                self.path.unlink(missing_ok=True)
                self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
                self.error = ""
            except OSError as ex:
                self.error = str(ex)
        return n

    def state(self) -> dict:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {"count": self.count, "bytes": size, "keep": self.keep, "days": self.days, "error": self.error}
