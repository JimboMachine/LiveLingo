"""框選翻譯相簿（2.16.0）：框選翻譯（Ctrl+Shift+F）的截圖和翻好的每一段一起存下來，
商店頁、技能說明、任務說明之後還能再看。只存在這台電腦上；隱私暫停時不存。"""

import json
import threading
import time
from pathlib import Path

FOLDER = "album"
INDEX = "album.json"
MAX_ITEMS = 200
MAX_WIDTH = 1280      # 截圖存的時候縮到最寬這麼多


class Album:
    def __init__(self, folder: Path, now=time.time):
        self.dir = Path(folder) / FOLDER
        self.now = now
        self._lock = threading.Lock()

    def _index(self) -> list[dict]:
        try:
            data = json.loads((self.dir / INDEX).read_text(encoding="utf-8"))
            return [i for i in data if isinstance(i, dict) and i.get("id")]
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / INDEX).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def add(self, bgr, pairs: list[tuple[str, str]], game: str = "") -> str:
        """bgr：截圖；pairs：[(原文, 譯文)]。回傳這一張的編號，存不了就是空字串。"""
        import cv2

        pairs = [(s.strip()[:1000], d.strip()[:1000]) for s, d in pairs if s and s.strip()]
        if bgr is None or not getattr(bgr, "size", 0) or not pairs:
            return ""
        h, w = bgr.shape[:2]
        if w > MAX_WIDTH:
            bgr = cv2.resize(bgr, (MAX_WIDTH, max(1, int(h * MAX_WIDTH / w))), interpolation=cv2.INTER_AREA)
        ok, png = cv2.imencode(".png", bgr)
        if not ok:
            return ""
        aid = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.now())) + f"-{int(self.now() * 1000) % 1000:03d}"
        with self._lock:
            items = self._index()
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / f"{aid}.png").write_bytes(png.tobytes())
                items.append({"id": aid, "at": round(self.now(), 1), "game": (game or "").lower(),
                              "items": [{"src": s, "dst": d} for s, d in pairs]})
                for old in items[:-MAX_ITEMS]:
                    (self.dir / f"{old['id']}.png").unlink(missing_ok=True)
                self._save(items[-MAX_ITEMS:])
            except OSError:
                return ""
        return aid

    def list(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._index()))      # 最新的在前

    def image(self, aid: str) -> bytes | None:
        if not aid or not all(ch.isalnum() or ch == "-" for ch in aid):
            return None
        try:
            return (self.dir / f"{aid}.png").read_bytes()
        except OSError:
            return None

    def remove(self, aid: str) -> bool:
        with self._lock:
            items = self._index()
            keep = [i for i in items if i["id"] != aid]
            if len(keep) == len(items):
                return False
            (self.dir / f"{aid}.png").unlink(missing_ok=True)
            self._save(keep)
        return True
