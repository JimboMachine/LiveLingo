"""劇情書（2.16.0）：劇情對話框（框選區設成「劇情」）讀到的任務、劇情文字，翻好後一則一則記下來，
照遊戲分開，之後可以回頭讀、匯出成文字檔。只存在這台電腦上；隱私暫停時不記。"""

import json
import threading
import time
from pathlib import Path

FILE = "storybook.jsonl"
MAX_LINES = 5000


class StoryBook:
    def __init__(self, folder: Path, now=time.time):
        self.path = Path(folder) / FILE
        self.now = now
        self._lock = threading.Lock()

    def add(self, game: str, original: str, translated: str) -> bool:
        original, translated = original.strip()[:1000], translated.strip()[:1000]
        if not original or not translated:
            return False
        item = {"at": round(self.now(), 1), "game": (game or "").lower()[:120], "src": original, "dst": translated}
        with self._lock:
            items = self._read()
            if items and items[-1]["src"] == original and items[-1]["game"] == item["game"]:
                return False      # 同一段又讀到一次
            try:
                if len(items) >= MAX_LINES:
                    keep = items[-(MAX_LINES // 2):] + [item]
                    self.path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in keep), encoding="utf-8")
                else:
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError:
                return False
        return True

    def _read(self) -> list[dict]:
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
            if isinstance(item, dict) and item.get("src") and item.get("dst"):
                out.append(item)
        return out

    def games(self) -> list[dict]:
        """有記到劇情的遊戲：[{"game", "count", "last"}]，最近玩的在前。"""
        with self._lock:
            items = self._read()
        seen: dict[str, dict] = {}
        for i in items:
            g = seen.setdefault(i.get("game", ""), {"game": i.get("game", ""), "count": 0, "last": 0.0})
            g["count"] += 1
            g["last"] = max(g["last"], float(i.get("at") or 0))
        return sorted(seen.values(), key=lambda g: -g["last"])

    def read(self, game: str, offset: int = 0, limit: int = 100) -> dict:
        """這個遊戲的劇情，照時間順序（舊的在前，像一本書）。"""
        with self._lock:
            items = [i for i in self._read() if i.get("game", "") == (game or "").lower()]
        offset, limit = max(0, int(offset)), max(1, min(500, int(limit)))
        return {"game": game, "total": len(items), "items": items[offset:offset + limit]}

    def export(self, game: str, title: str, folder: Path) -> Path | None:
        items = self.read(game, 0, 100000)["items"]
        if not items:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in title if ch.isalnum() or ch in " -_") or "story"
        path = folder / f"{safe} - {time.strftime('%Y-%m-%d', time.localtime(self.now()))}.txt"
        lines = [title, ""]
        for i in items:
            lines += [time.strftime("%Y-%m-%d %H:%M", time.localtime(i["at"])), i["src"], i["dst"], ""]
        path.write_text("\n".join(lines), encoding="utf-8-sig")
        return path

    def clear(self, game: str) -> int:
        with self._lock:
            items = self._read()
            keep = [i for i in items if i.get("game", "") != (game or "").lower()]
            try:
                self.path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in keep), encoding="utf-8")
            except OSError:
                return 0
        return len(items) - len(keep)
