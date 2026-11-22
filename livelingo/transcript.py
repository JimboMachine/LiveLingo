"""字幕記錄：使用者自己按開始和結束，這段時間的原文和翻譯存成一個 txt 檔。"""

import threading
import time
from pathlib import Path

from .chatpaint import NAMES, speaker_name

TITLES = {
    "zh-TW": "字幕記錄", "zh-CN": "字幕记录", "en": "Subtitle log", "ko": "자막 기록", "ja": "字幕の記録",
}
COLON = {"zh-TW": "：", "zh-CN": "：", "ja": "："}


def _unique(folder: Path, stem: str) -> Path:
    path, n = folder / f"{stem}.txt", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).txt", n + 1
    return path


def render(items: list[dict], ui_lang: str, started: float, ended: float, with_original: bool = True) -> str:
    """把記下來的訊息排成文字。每則一段：時間、誰說的、原文，下一行是翻譯。"""
    names = NAMES.get(ui_lang) or NAMES["en"]
    colon = COLON.get(ui_lang, ": ")
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(started))
    lines = [f"LiveLingo {TITLES.get(ui_lang) or TITLES['en']}",
             f"{day} ~ {time.strftime('%H:%M', time.localtime(ended))}", ""]
    for it in items:
        who = names["me"] if it["who"] == "me" else names["screen" if it["via"] == "screen" else "heard"]
        if it.get("speaker") and it["who"] != "me":      # 有分出是誰說的：寫那個人的名字
            who = speaker_name(it["speaker"], ui_lang, it.get("voice") or "")
        stamp = time.strftime("%H:%M:%S", time.localtime(it["at"]))
        original, translated = it["original"].strip(), (it.get("translated") or "").strip()
        same = not translated or translated == original
        if same or not with_original:
            lines.append(f"[{stamp}] {who}{colon}{original if same else translated}")
        else:
            lines.append(f"[{stamp}] {who}{colon}{original}")
            lines.append(f"→ {translated}")
        lines.append("")
    return "\r\n".join(lines)


class Transcript:
    def __init__(self):
        self._lock = threading.Lock()
        self.on = False
        self.closing = False                   # 按了結束，正在等最後幾句翻完
        self.since = 0.0
        self._items: dict[int, dict] = {}      # 訊息編號 → 最新的內容（翻譯好了會再更新一次）
        self.last: Path | None = None          # 上一次存的檔案
        self.last_count = 0

    def start(self) -> None:
        with self._lock:
            self.on, self.closing, self.since, self._items = True, False, time.time(), {}

    def add(self, msg: dict) -> None:
        """記錄中才收。同一則訊息翻好之後會再來一次，時間用第一次看到它的時間。"""
        with self._lock:
            if not self.on:
                return
            old = self._items.get(msg["id"])
            if self.closing and not old:
                return      # 已經按了結束：只等原本那幾句翻完，新的不收
            self._items[msg["id"]] = {"who": msg["who"], "via": msg["via"], "original": msg["original"],
                                      "translated": "" if msg.get("error") else msg.get("translated", ""),
                                      "pending": bool(msg.get("pending")),
                                      "at": old["at"] if old else time.time(),
                                      # 分辨說話者：第幾個人、使用者幫他取的名字（只寫進使用者自己要存的這個檔）
                                      "speaker": int(msg.get("speaker") or 0), "voice": str(msg.get("voice") or "")}

    def name_speaker(self, speaker: int, name: str) -> None:
        """使用者幫第幾個說話者改了名字：已經記下來的句子也跟著改。"""
        with self._lock:
            for it in self._items.values():
                if it.get("speaker") == speaker:
                    it["voice"] = name

    def forget_speakers(self) -> None:
        """按了「全部重新分辨」：之前的編號不算數了，已經記下來的句子不再寫是誰（不然新的 1 號和舊的 1 號會混在一起）。"""
        with self._lock:
            for it in self._items.values():
                it["speaker"], it["voice"] = 0, ""

    def drop(self, msg_id: int) -> None:
        with self._lock:
            self._items.pop(msg_id, None)

    @property
    def count(self) -> int:
        return len(self._items)

    def has_pending(self) -> bool:
        with self._lock:
            return any(it["pending"] for it in self._items.values())

    def close(self, wait: float = 20.0) -> None:
        """按了結束：不再收新的句子，最多等 wait 秒讓還在翻的那幾句翻完，檔案裡才不會只有原文。"""
        self.closing = True
        deadline = time.time() + wait
        while time.time() < deadline and self.has_pending():
            time.sleep(0.1)

    def stop(self, folder: Path, ui_lang: str, with_original: bool = True) -> Path | None:
        """
        結束記錄並存檔。這段時間一句都沒有就不存，回傳 None。
        存檔失敗（資料夾不能寫）會丟出 OSError，記錄照樣留著，換個資料夾可以再存一次。
        """
        with self._lock:
            if not self.on:
                return None
            items, started = list(self._items.values()), self.since
            path = None
            if items:
                folder.mkdir(parents=True, exist_ok=True)
                path = _unique(folder, "LiveLingo " + time.strftime("%Y-%m-%d %H.%M", time.localtime(started)))
                # 加上 BOM，舊版記事本才不會把中文和韓文顯示成亂碼
                path.write_text(render(items, ui_lang, started, time.time(), with_original),
                                encoding="utf-8-sig", newline="")
            self.on, self.closing, self._items = False, False, {}
            self.last, self.last_count = path, len(items)
        return path

    def state(self, folder: Path) -> dict:
        return {"on": self.on, "saving": self.closing, "since": self.since, "count": self.count, "folder": str(folder),
                "file": str(self.last) if self.last else "", "name": self.last.name if self.last else "",
                "saved": self.last_count}
