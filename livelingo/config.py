"""設定檔和詞彙表（都存成 JSON，放在使用者的資料夾）。"""

import json
import locale
import os
import threading
from pathlib import Path

UI_LANGS = ("zh-TW", "zh-CN", "en", "ko", "ja")      # 翻譯語言有幾種，介面就有幾種（有測試在看）

DEFAULTS = {
    "ui_lang": None,             # 介面語言；None = 第一次開啟時照 Windows 的語言決定
    "my_lang": "zh-TW",          # 我看得懂的語言
    "their_lang": "ko",          # 對方用的語言（開了自動偵測時：目前判斷到的語言）
    "their_auto": False,         # 自動偵測對方用的語言
    "transcript_dir": "",        # 字幕記錄存在哪個資料夾（空的：文件\\LiveLingo）
    "transcript_original": True, # 字幕記錄裡同時留原文
    "tone": "casual",            # casual = 朋友語氣（韓文半語）；polite = 有禮貌
    "quality": "auto",           # auto = 照顯示卡自動決定；standard；high
    "game_terms": True,          # 內建的遊戲用語表（英雄聯盟，韓文和中文之間）
    "speaker_device": None,      # 對方的聲音從哪個播放裝置出來（裝置 id），None = Windows 預設
    "speaker_name": "",
    "mic_device": None,          # 我用哪支麥克風（裝置 id），None = Windows 預設
    "mic_name": "",
    "region": None,              # 螢幕上框選的聊天區 {"left","top","width","height"}
    "also_english": False,       # 螢幕上的英文訊息也翻譯
    "sources": {"screen": False, "listen": False, "mic": False},
    "on_top": True,
    "show_pronunciation": True,  # 韓文下面顯示唸法
    "copy_spoken": True,         # 我用說的翻出來的句子也自動複製
    "setup_done": False,
    "window": {"w": 980, "h": 720, "x": None, "y": None},
    "theme": "dark",             # 介面主題：dark / light / system（跟隨 Windows）
    "accent": "mint",            # 強調色：mint / blue / violet / rose / cyan
    "layout": 2,                 # 2 = 左邊有固定導覽的版面（2.3.0 起）；舊設定檔沒有這個值
    "overlay": True,             # 浮動留言：翻譯像實況聊天室一樣直接顯示在遊戲畫面上
    "overlay_rect": None,        # 浮動留言的位置和大小 [x, y, 寬, 高]，None = 預設位置
    "overlay_font": 24,          # 字體大小
    "overlay_seconds": 15,       # 一則留言停留幾秒，0 = 不會自己消失
    "overlay_backdrop": 0,       # 字後面的深色底有多不透明（0 到 80），0 = 只有描邊
    "overlay_original": False,   # 翻譯下面用小字顯示原文
    "overlay_mine": True,        # 我自己說的、打的也顯示
    # ---- 進階，介面上沒有 ----
    "interval": 0.7,             # 每隔幾秒看一次螢幕
    "ocr_scale": 1.5,            # 辨識前先把畫面放大，小字比較準
    "end_silence": 0.6,          # 停頓多久算一句話說完（秒）
    "whisper_threads": 4,
    "threads": 0,                # 翻譯引擎用幾個 CPU 核心，0 = 自動
}

# 介面可以改的設定（其他的只能直接改檔案）
THEMES = ("dark", "light", "system")
ACCENTS = ("mint", "blue", "violet", "rose", "cyan")
EDITABLE = {"ui_lang", "theme", "accent", "my_lang", "their_lang", "their_auto", "transcript_original", "tone", "quality", "game_terms", "speaker_device", "speaker_name",
            "mic_device", "mic_name", "also_english", "on_top", "show_pronunciation", "copy_spoken", "setup_done",
            "overlay", "overlay_font", "overlay_seconds", "overlay_backdrop", "overlay_original", "overlay_mine"}


def system_ui_lang() -> str:
    """照作業系統的語言挑介面語言。"""
    name = ""
    try:
        if os.name == "nt":
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid, "")
        else:
            name = locale.getlocale()[0] or os.environ.get("LANG", "")
    except Exception:  # noqa: BLE001
        pass
    name = (name or "").lower()
    if name.startswith("zh"):
        simplified = any(mark in name for mark in ("cn", "sg", "hans", "simplified"))
        return "zh-CN" if simplified else "zh-TW"
    if name.startswith("ko"):
        return "ko"
    if name.startswith("ja"):
        return "ja"
    return "en"


def _write_json(path: Path, value) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Config:
    def __init__(self, folder: Path):
        self.path = folder / "config.json"
        self._lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in DEFAULTS and isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
                    self.data[k].update(v)
                elif k in DEFAULTS:
                    self.data[k] = v
        except Exception:  # noqa: BLE001  沒有設定檔或檔案壞了就用預設值
            pass
        else:
            if "layout" not in saved:      # 從舊版升上來：原本是很窄的視窗，新版面需要寬一點，只調這一次
                self.data["layout"] = 1
        if self.data["ui_lang"] not in UI_LANGS:
            self.data["ui_lang"] = system_ui_lang()
        if self.data["layout"] < 2:
            if self.data["window"]["w"] < 900:
                self.data["window"] = dict(DEFAULTS["window"])
            self.data["layout"] = 2

    def __getitem__(self, key):
        with self._lock:
            return self.data[key]

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))

    def set(self, **changes) -> None:
        with self._lock:
            self.data.update(changes)
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                _write_json(self.path, self.data)
            except OSError:
                pass


GLOSSARY_SEED = [
    {"src": "쵸비", "dst": "Chovy"}, {"src": "초비", "dst": "Chovy"}, {"src": "Chovy", "dst": "쵸비"},
    {"src": "페이커", "dst": "Faker"}, {"src": "Faker", "dst": "페이커"}, {"src": "롤드컵", "dst": "世界大賽"},
]


class Glossary:
    """我的詞彙表：[{"src": 看到這個詞, "dst": 要翻成}]。"""

    def __init__(self, folder: Path):
        self.path = folder / "glossary.json"
        self._lock = threading.RLock()
        self.items: list[dict] = [dict(i) for i in GLOSSARY_SEED]   # 第一次使用時先放幾個例子
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [{"src": str(i["src"]).strip(), "dst": str(i["dst"]).strip()} for i in raw
                          if str(i.get("src", "")).strip() and str(i.get("dst", "")).strip()]
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            self.items = []

    def as_dict(self) -> dict[str, str]:
        with self._lock:
            return {i["src"]: i["dst"] for i in self.items}

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(i) for i in self.items]

    def add(self, src: str, dst: str) -> bool:
        src, dst = src.strip()[:80], dst.strip()[:80]
        if not src or not dst:
            return False
        with self._lock:
            self.items = [i for i in self.items if i["src"].lower() != src.lower()]
            self.items.insert(0, {"src": src, "dst": dst})
            self._save()
        return True

    def remove(self, src: str) -> None:
        with self._lock:
            self.items = [i for i in self.items if i["src"] != src]
            self._save()

    def _save(self) -> None:
        try:
            _write_json(self.path, self.items)
        except OSError:
            pass


class Lessons:
    """教過的句子：[{"src": 原文, "dst": 使用者要的翻法, "lang": 原文的語言, "target": 翻成哪個語言}]。
    一模一樣的句子之後直接用這個翻法，類似的句子會把它當範例給模型參考。"""

    LIMIT = 400

    def __init__(self, folder: Path):
        self.path = folder / "lessons.json"
        self._lock = threading.RLock()
        self.items: list[dict] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [{"src": str(i["src"]).strip(), "dst": str(i["dst"]).strip(), "lang": str(i["lang"]),
                           "target": str(i["target"])} for i in raw
                          if str(i.get("src", "")).strip() and str(i.get("dst", "")).strip()][: self.LIMIT]
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            self.items = []

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(i) for i in self.items]

    def add(self, src: str, dst: str, lang: str, target: str) -> bool:
        src, dst = src.strip()[:500], dst.strip()[:500]
        if not src or not dst or src == dst:
            return False
        with self._lock:
            self.items = [i for i in self.items if not (i["src"] == src and i["target"] == target)]
            self.items.insert(0, {"src": src, "dst": dst, "lang": lang, "target": target})
            del self.items[self.LIMIT:]
            self._save()
        return True

    def remove(self, src: str, target: str) -> None:
        with self._lock:
            self.items = [i for i in self.items if not (i["src"] == src and i["target"] == target)]
            self._save()

    def _save(self) -> None:
        try:
            _write_json(self.path, self.items)
        except OSError:
            pass
