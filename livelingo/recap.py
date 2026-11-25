"""
對話摘要（2.10.0）：一場遊戲、一通電話結束後（安靜超過 10 分鐘），用翻譯模型把剛才的對話整理成幾個重點，
用我的語言寫。存在這台電腦上（recaps.json），在「對話歷史」頁看得到、搜得到，也可以自己按「現在摘要」。
"""

import json
import threading
import time
from pathlib import Path

FILE = "recaps.json"
GAP = 600                  # 安靜超過這麼久（秒）就算一段對話結束了
MIN_LINES = 6              # 自動摘要至少要有這麼多句（而且要有對方說的）
MAX_RECAPS = 500


def sessions(rows: list[dict], gap: float = GAP) -> list[list[dict]]:
    """照時間排好的句子 → 一段一段的對話（中間安靜超過 gap 秒就切開）。"""
    out: list[list[dict]] = []
    last = None
    for row in rows:
        at = float(row.get("at") or 0)
        if last is None or at - last > gap:
            out.append([])
        out[-1].append(row)
        last = at
    return out


def worth(lines: list[dict], least: int = MIN_LINES) -> bool:
    return len(lines) >= least and any(r.get("who") != "me" for r in lines)


class Recaps:
    """[{"id", "start", "end", "lines"（幾句）, "lang"（摘要用的語言）, "points": [...]}]，舊的在前面。"""

    def __init__(self, folder: Path, now=time.time):
        self.path = folder / FILE
        self._now = now
        self._lock = threading.RLock()
        self.items: list[dict] = []
        self.error = ""
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as ex:
            self.error = str(ex)
            return
        for row in data if isinstance(data, list) else []:
            try:
                points = [str(p)[:300] for p in row["points"] if str(p).strip()][:8]
                if points:
                    self.items.append({"id": str(row["id"]), "start": float(row["start"]), "end": float(row["end"]),
                                       "lines": int(row.get("lines") or 0), "lang": str(row.get("lang") or ""), "points": points})
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> bool:
        try:
            if not self.items:
                self.path.unlink(missing_ok=True)
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self.error = ""
            return True
        except OSError as ex:
            self.error = str(ex)
            return False

    @property
    def until(self) -> float:
        """摘要過的對話最後一句的時間：比這個晚的才是還沒摘要的。"""
        with self._lock:
            return max((r["end"] for r in self.items), default=0.0)

    def add(self, lines: list[dict], points: list[str], lang: str) -> dict | None:
        if not lines or not points:
            return None
        start, end = float(lines[0].get("at") or 0), float(lines[-1].get("at") or 0)
        row = {"id": f"{int(start)}-{int(end)}", "start": start, "end": end, "lines": len(lines), "lang": lang, "points": list(points)}
        with self._lock:
            self.items = [r for r in self.items if r["id"] != row["id"]] + [row]
            self.items.sort(key=lambda r: r["end"])
            del self.items[:-MAX_RECAPS]
            self._save()
        return dict(row)

    def remove(self, rid: str) -> bool:
        with self._lock:
            before = len(self.items)
            self.items = [r for r in self.items if r["id"] != str(rid)]
            if len(self.items) == before:
                return False
            self._save()
            return True

    def search(self, q: str = "") -> list[dict]:
        """最新的在前面。q：每個詞都要出現在重點裡。"""
        words = [w for w in str(q or "").lower().split() if w]
        with self._lock:
            rows = [dict(r, points=list(r["points"])) for r in reversed(self.items)]
        if words:
            rows = [r for r in rows if all(w in "\n".join(r["points"]).lower() for w in words)]
        return rows

    def clear(self) -> int:
        with self._lock:
            n = len(self.items)
            self.items = []
            self._save()
            return n

    def state(self) -> dict:
        return {"count": len(self.items), "error": self.error}
