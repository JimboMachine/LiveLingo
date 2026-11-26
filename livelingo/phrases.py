"""
常用句庫（2.11.0）：先存好常說的話（「我去下路」「等我一下」「gg」），每一句事先翻成對方的語言。
按快速鍵叫出小框，按數字就複製翻好的那句，不用等模型。只存在這台電腦上（phrases.json）。
- 每一句記我的語言的原文，和翻成各個語言的結果（tr：語言代碼 → 譯文）。對方的語言換了，缺的在背景補翻。
- 譯文可以自己改（fixed）：改過的不會被重翻蓋掉。
"""

import json
import threading
import uuid
from pathlib import Path

FILE = "phrases.json"
MAX_PHRASES = 36
MAX_TEXT = 120
PAGE = 9          # 小框一頁放幾句（按 1 到 9）

STARTERS = {
    "zh-TW": ["等我一下", "我馬上到", "我去下路", "好球！", "謝謝", "不好意思，剛剛沒看到", "gg"],
    "zh-CN": ["等我一下", "我马上到", "我去下路", "好球！", "谢谢", "不好意思，刚刚没看到", "gg"],
    "en": ["Give me a sec", "On my way", "Going bot lane", "Nice one!", "Thanks", "Sorry, I didn't see that", "gg"],
    "ko": ["잠깐만요", "금방 갈게요", "봇 갈게요", "나이스!", "고마워요", "미안해요, 못 봤어요", "gg"],
    "ja": ["ちょっと待って", "すぐ行きます", "ボットに行きます", "ナイス！", "ありがとう", "ごめん、見てなかった", "gg"],
}


def _clean(text) -> str:
    return " ".join(str(text or "").split())[:MAX_TEXT]


class Phrasebook:
    """{"items": [{id, text, lang（原文的語言）, tr: {語言代碼: 譯文}, fixed: [改過譯文的語言代碼]}]}"""

    def __init__(self, folder: Path):
        self.path = folder / FILE
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
        for row in data.get("items", []) if isinstance(data, dict) else []:
            try:
                text = _clean(row["text"])
                if not text:
                    continue
                tr = {str(k): _clean(v) for k, v in (row.get("tr") or {}).items() if _clean(v)}
                self.items.append({"id": str(row.get("id") or uuid.uuid4().hex[:8]), "text": text, "lang": str(row.get("lang") or ""),
                                   "tr": tr, "fixed": [str(k) for k in row.get("fixed") or [] if str(k) in tr]})
            except (KeyError, TypeError, AttributeError, ValueError):
                continue
        del self.items[MAX_PHRASES:]

    def _save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"items": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self.error = ""
            return True
        except OSError as ex:
            self.error = str(ex)
            return False

    def _find(self, pid: str) -> dict | None:
        return next((x for x in self.items if x["id"] == pid), None)

    def add(self, text: str, lang: str = "") -> dict | None:
        """加一句。空的、已經有一樣的、滿了：回傳 None。"""
        text = _clean(text)
        with self._lock:
            if not text or len(self.items) >= MAX_PHRASES or any(x["text"].lower() == text.lower() for x in self.items):
                return None
            row = {"id": uuid.uuid4().hex[:8], "text": text, "lang": lang, "tr": {}, "fixed": []}
            self.items.append(row)
            self._save()
            return dict(row)

    def starters(self, ui_lang: str, lang: str = "") -> int:
        """放進幾句起手的（照介面語言；沒有那個語言就用英文）。回傳真的加進幾句。"""
        return sum(1 for text in STARTERS.get(ui_lang, STARTERS["en"]) if self.add(text, lang))

    def remove(self, pid: str) -> bool:
        with self._lock:
            row = self._find(pid)
            if row is None:
                return False
            self.items.remove(row)
            self._save()
            return True

    def set_translation(self, pid: str, lang: str, text: str, fixed: bool = False) -> bool:
        """記下譯成 lang 的結果。自己改過的（fixed）之後機器補翻不會蓋掉；改成空的 = 拿掉改過的，回到沒有（會重翻）。"""
        text = _clean(text)
        with self._lock:
            row = self._find(pid)
            if row is None:
                return False
            if not fixed and lang in row["fixed"]:
                return False
            if text:
                row["tr"][lang] = text
                if fixed and lang not in row["fixed"]:
                    row["fixed"].append(lang)
            else:
                row["tr"].pop(lang, None)
                if lang in row["fixed"]:
                    row["fixed"].remove(lang)
            self._save()
            return True

    def missing(self, lang: str) -> list[dict]:
        """還沒有譯成 lang 的句子。"""
        with self._lock:
            return [dict(x) for x in self.items if lang not in x["tr"]]

    def listing(self, lang: str) -> list[dict]:
        """給介面和小框：每一句的原文和譯成 lang 的結果（還沒有就是空字串）。"""
        with self._lock:
            return [{"id": x["id"], "text": x["text"], "tr": x["tr"].get(lang, ""), "fixed": lang in x["fixed"]} for x in self.items]

    def state(self, lang: str) -> dict:
        return {"items": self.listing(lang), "lang": lang, "max": MAX_PHRASES, "error": self.error}
