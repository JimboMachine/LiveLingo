"""改正紀錄和每週錯誤報告（2.15.0）。

使用者每次改正 LiveLingo（聽錯、看錯的原文改對；整句教它怎麼翻；詞彙表加一個詞）都記一行在 corrections.jsonl，
報告列出最近 7 天最常改的十個，知道哪裡最值得教。只存在這台電腦上；隱私暫停的時候不記。
"""

import json
import threading
import time
from collections import Counter
from pathlib import Path

FILE = "corrections.jsonl"
KINDS = ("heard", "seen", "sentence", "word")      # 聽錯 / 看錯（改原文）、整句教、詞彙表
MAX_LINES = 3000
WEEK = 7 * 86400
TOP = 10


class Corrections:
    def __init__(self, folder: Path, now=time.time):
        self.path = Path(folder) / FILE
        self.now = now
        self._lock = threading.Lock()

    def add(self, kind: str, wrong: str, right: str, lang: str = "", target: str = "") -> bool:
        wrong, right = " ".join(str(wrong or "").split())[:200], " ".join(str(right or "").split())[:200]
        if kind not in KINDS or not right or wrong == right:
            return False
        line = json.dumps({"at": round(self.now(), 1), "kind": kind, "wrong": wrong, "right": right, "lang": lang, "target": target},
                          ensure_ascii=False)
        with self._lock:
            try:
                old = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
                if len(old) >= MAX_LINES:
                    self.path.write_text("\n".join(old[-(MAX_LINES // 2):] + [line]) + "\n", encoding="utf-8")
                else:
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(line + "\n")
            except OSError:
                return False
        return True

    def items(self, since: float = 0.0) -> list[dict]:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
        out = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("kind") in KINDS and float(item.get("at") or 0) >= since:
                out.append(item)
        return out

    def report(self, days: int = 7) -> dict:
        """最近 days 天：每一種改了幾次，最常改的 TOP 個（同一個錯改了幾次）。
        同一個錯：聽錯、看錯的照「錯的 → 對的」算；整句、詞彙照原文算（同一句教了好幾次也是同一個）。"""
        since = self.now() - days * 86400
        items = self.items(since)
        kinds = Counter(i["kind"] for i in items)
        groups: dict[tuple, dict] = {}
        count: Counter = Counter()
        for i in items:
            key = (i["kind"], i["wrong"].lower(), i["right"].lower()) if i["kind"] in ("heard", "seen") else (i["kind"], i["wrong"].lower())
            count[key] += 1
            groups[key] = {"kind": i["kind"], "wrong": i["wrong"], "right": i["right"], "last": i["at"]}      # 最後一次的改法
        top = [dict(groups[k], n=n) for k, n in sorted(count.items(), key=lambda kv: (-kv[1], -groups[kv[0]]["last"]))[:TOP]]
        return {"days": days, "total": len(items), "kinds": {k: kinds.get(k, 0) for k in KINDS}, "top": top}

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink()
            except OSError:
                pass
