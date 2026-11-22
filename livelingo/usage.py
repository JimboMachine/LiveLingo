"""
使用數字：每天翻了幾句、失敗幾次、平均等多久。只有數字，沒有任何對話內容；只存在這台電腦上（usage.json），不會上傳。

一句話只算一次（重翻、改原文重翻不再算）。等多久 = 從這句話出現（語音：說完）到翻譯顯示出來的秒數。
可以整個關掉不記，也可以一鍵清除。最多留 KEEP_DAYS 天。
"""

import json
import threading
import time
from pathlib import Path

FILE = "usage.json"
KEEP_DAYS = 90
SAVE_EVERY = 15.0                  # 最多隔幾秒寫一次檔（聊天很熱鬧的時候不要每一行都寫）
VIAS = ("heard", "screen", "spoken", "typed")
CHART_DAYS = 14


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def _empty() -> dict:
    return {**{via: 0 for via in VIAS}, "failed": 0, "wait": 0.0, "waited": 0}


class Usage:
    def __init__(self, folder: Path, keep: bool = True, now=time.time):
        self.path = folder / FILE
        self.keep = bool(keep)
        self._now = now
        self._lock = threading.Lock()
        self._days: dict[str, dict] = {}
        self._dirty = False
        self._saved_at = 0.0
        self.error = ""
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for day, row in (raw.get("days") or {}).items():
                time.strptime(day, "%Y-%m-%d")
                clean = _empty()
                for key in clean:
                    value = row.get(key, 0)
                    clean[key] = max(0.0, float(value)) if key == "wait" else max(0, int(value))
                self._days[day] = clean
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001  （檔案壞了：當作沒有，從頭開始記）
            self._days = {}
        self._trim()

    def _trim(self) -> None:
        oldest = _day(self._now() - (KEEP_DAYS - 1) * 86400)
        self._days = {day: row for day, row in sorted(self._days.items()) if day >= oldest}

    def add(self, via: str, wait: float | None = None, failed: bool = False) -> None:
        """記一句。via：heard / screen / spoken / typed。wait：等了幾秒（失敗的不算進平均）。"""
        if not self.keep or via not in VIAS:
            return
        with self._lock:
            row = self._days.setdefault(_day(self._now()), _empty())
            row[via] += 1
            if failed:
                row["failed"] += 1
            elif wait is not None and 0 <= wait < 600:
                row["wait"] += float(wait)
                row["waited"] += 1
            self._dirty = True
            if self._now() - self._saved_at >= SAVE_EVERY:
                self._save()

    def flush(self) -> None:
        """還沒寫進檔案的現在寫（關程式的時候叫）。"""
        with self._lock:
            if self._dirty:
                self._save()

    def _save(self) -> None:
        self._trim()
        self._saved_at = self._now()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"days": {day: {**row, "wait": round(row["wait"], 2)} for day, row in self._days.items()}}), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty, self.error = False, ""
        except OSError as ex:
            self.error = str(ex)

    def configure(self, keep: bool) -> None:
        """關掉只是不再記新的；已經記的要按清除。"""
        self.keep = bool(keep)

    def clear(self) -> int:
        """全部清除。回傳清掉幾天的數字。"""
        with self._lock:
            n = len(self._days)
            self._days, self._dirty = {}, False
            try:
                self.path.unlink(missing_ok=True)
                self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
                self.error = ""
            except OSError as ex:
                self.error = str(ex)
        return n

    def _sum(self, days: int) -> dict:
        today = self._now()
        wanted = {_day(today - n * 86400) for n in range(days)}
        total = _empty()
        for day, row in self._days.items():
            if day in wanted:
                for key in total:
                    total[key] += row[key]
        count = sum(total[via] for via in VIAS)
        return {"count": count, **{via: total[via] for via in VIAS}, "failed": total["failed"],
                "wait": round(total["wait"] / total["waited"], 1) if total["waited"] else None}

    def summary(self) -> dict:
        """today / week（最近 7 天）/ month（最近 30 天）各一組：count 幾句、各來源幾句、failed 失敗幾次、wait 平均等幾秒（沒有就是 None）。
        days：最近 CHART_DAYS 天每天幾句（沒用的那天是 0），舊的在前。since：最早有數字的那一天。"""
        with self._lock:
            today = self._now()
            chart = []
            for n in range(CHART_DAYS - 1, -1, -1):
                day = _day(today - n * 86400)
                row = self._days.get(day) or _empty()
                chart.append({"day": day, "count": sum(row[via] for via in VIAS), "failed": row["failed"]})
            return {"keep": self.keep, "today": self._sum(1), "week": self._sum(7), "month": self._sum(30), "days": chart,
                    "since": min(self._days) if self._days else "", "error": self.error}
