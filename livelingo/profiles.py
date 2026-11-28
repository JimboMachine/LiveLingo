"""多份設定檔（2.20.0）：把現在的設定存成一個名字（「打遊戲」「看影片」「開會」），之後一鍵換過去。
語言、聊天區、顯示、語音、翻譯品質這些都算；介面語言、字幕資料夾、詞彙、玩家備註這些「我的東西」不算，換設定檔不會動到。"""

import json
import re
import threading
from pathlib import Path

MAX_PROFILES = 8
SKIP = ("window", "layout", "hotkeys_v", "setup_done", "tray_hint_shown", "seen_version", "region", "sources", "store_dir", "store_leftover",
        "stream_key", "ui_lang", "transcript_dir", "keep_words", "name_skip", "abbr_skip", "game_profiles", "player_notes", "tips_seen",
        "review_reminded", "speech_picked", "overlay_layouts", "hotkeys")


def clean_name(name) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:30]


class Profiles:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data = {clean_name(k): v for k, v in data.items() if clean_name(k) and isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            self._data = {}

    def pick(self, values: dict) -> dict:
        return {k: v for k, v in values.items() if k not in SKIP}

    def names(self) -> list[str]:
        with self._lock:
            return list(self._data)

    def get(self, name: str) -> dict | None:
        with self._lock:
            row = self._data.get(clean_name(name))
            return json.loads(json.dumps(row)) if row is not None else None

    def save(self, name: str, values: dict) -> str | None:
        """存（同名就覆蓋）。名字是空的、或已經滿了（不同名）回傳 None。"""
        name = clean_name(name)
        with self._lock:
            if not name or (name not in self._data and len(self._data) >= MAX_PROFILES):
                return None
            self._data[name] = json.loads(json.dumps(self.pick(values)))
            self._write()
        return name

    def remove(self, name: str) -> bool:
        with self._lock:
            if self._data.pop(clean_name(name), None) is None:
                return False
            self._write()
        return True

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
