"""設定檔和詞彙表（都存成 JSON，放在使用者的資料夾）。"""

import json
import locale
import os
import threading
import time
from pathlib import Path

UI_LANGS = ("zh-TW", "zh-CN", "en", "ko", "ja")      # 翻譯語言有幾種，介面就有幾種（有測試在看）

DEFAULTS = {
    "ui_lang": None,             # 介面語言；None = 第一次開啟時照 Windows 的語言決定
    "my_lang": "zh-TW",          # 我看得懂的語言
    "their_lang": "ko",          # 對方用的語言（開了自動偵測時：目前判斷到的語言）
    "their_auto": False,         # 自動偵測對方用的語言
    "transcript_dir": "",        # 字幕記錄存在哪個資料夾（空的：文件\\LiveLingo）
    "transcript_original": True, # 字幕記錄裡同時留原文
    "close_to_tray": True,       # 關掉視窗時收到系統匣，不結束程式
    "tray_hint_shown": False,    # 第一次收到系統匣時提醒過了沒
    "tone": "casual",            # casual = 朋友語氣（韓文半語）；polite = 有禮貌
    "quality": "auto",           # auto = 照顯示卡自動決定；standard；high
    "game_terms": True,          # 內建的遊戲用語表（英雄聯盟，韓文和中文之間）
    "term_packs": ["lol", "chat"],   # 用哪幾包內建用語（terms.PACKS）；game_terms 關掉時都不用
    "back_translate": True,      # 我說的、打的翻成對方的語言之後，再翻回來給我看（回譯）
    "use_context": True,         # 翻譯時參考前面兩句對話（代名詞、省略的主詞比較不會翻錯）
    "speaker_device": None,      # 對方的聲音從哪個播放裝置出來（裝置 id），None = Windows 預設
    "speaker_name": "",
    "mic_device": None,          # 我用哪支麥克風（裝置 id），None = Windows 預設
    "mic_name": "",
    "listen_apps": [],           # 只聽這幾個程式的聲音（程式檔名，例如 Discord.exe）；空的 = 整台電腦播放的聲音
    "voice_sensitivity": 3,      # 多小聲算有人在說話：1（很吵的環境，只收清楚的聲音）到 5（小聲說話也收）
    "end_silence": 0.6,          # 停頓多久算一句話說完（秒），0.3 到 1.5
    "speech_device": "cpu",      # 語音辨識用 cpu 處理器 / gpu 顯示卡（要另外下載 cuDNN，不行會自動退回處理器）
    "speech_mode": "fast",       # 語音辨識：fast 比較快 / accurate 比較準（慢大約一倍）
    "drop_echo": True,           # 麥克風收到喇叭播出來的對方聲音時，不要當成我說的話
    "ptt": False,                # 按住才說話：只有按著 ptt_key 的時候麥克風才收音
    "ptt_key": "F8",             # oswin.PTT_KEYS 裡的一個
    "region": None,              # 舊版只有一個聊天區的時候用的；開程式時會搬進 regions，之後都是 None
    # 螢幕上框選的聊天區，可以好幾個：{"id","name","left","top","width","height","on",
    #   "app"（跟著哪個程式的視窗，空的 = 固定在螢幕上的位置）,"anchor"（框的時候那個視窗的左上角 [x, y]）}
    "regions": [],
    "also_english": False,       # 螢幕上的英文訊息也翻譯
    "sources": {"screen": False, "listen": False, "mic": False},
    "on_top": True,
    "show_pronunciation": True,  # 韓文下面顯示唸法
    "copy_spoken": True,         # 我用說的翻出來的句子也自動複製
    "setup_done": False,
    # 全域快速鍵（hotkeys.ACTIONS）：開關字幕、開關麥克風、複製我上一句的翻譯、叫出快速輸入框。空字串 = 不用
    "hotkeys": {"overlay": "Ctrl+Alt+S", "mic": "Ctrl+Alt+M", "copy": "Ctrl+Alt+C", "quick": "Ctrl+Alt+T"},
    "window": {"w": 980, "h": 720, "x": None, "y": None},
    "theme": "dark",             # 介面主題：dark / light / system（跟隨 Windows）
    "accent": "mint",            # 強調色：mint / blue / violet / rose / cyan
    "layout": 2,                 # 2 = 左邊有固定導覽的版面（2.3.0 起）；舊設定檔沒有這個值
    "overlay": True,             # 浮動留言：翻譯像實況聊天室一樣直接顯示在遊戲畫面上
    "overlay_rect": None,        # 浮動留言的位置和大小 [x, y, 寬, 高]，None = 預設位置
    "overlay_form": "float",     # 顯示在畫面上的樣子：float 浮動留言（位置自己擺）/ bar 底部字幕條（位置自動）
    "overlay_monitor": 0,        # 顯示在第幾個螢幕（oswin.monitors() 的順序，0 = 主螢幕）
    "overlay_layouts": [],       # 自己存的版面：[{"name","look":{外觀},"rect","form","monitor"}]，最多 MAX_LAYOUTS 組
    "overlay_font": 24,          # 字體大小
    "overlay_seconds": 15,       # 一則留言停留幾秒，0 = 不會自己消失
    "overlay_backdrop": 0,       # 字後面的深色底有多不透明（0 到 80），0 = 只有描邊
    "overlay_original": False,   # 翻譯下面用小字顯示原文
    "overlay_mine": True,        # 我自己說的、打的也顯示
    "overlay_family": "sans",    # 字型：sans 黑體 / serif 明體 / kai 楷體
    "overlay_bold": True,        # 粗體
    "overlay_color": "#FFFFFF",  # 字的顏色
    "overlay_outline": 2,        # 描邊粗細，0（沒有）到 4
    "overlay_names": True,       # 每則前面顯示是誰說的（對方／聊天／我）
    "overlay_name_colors": {"heard": "#C494FF", "screen": "#FFC45C", "me": "#5CDEFF"},
    "overlay_opacity": 100,      # 整體不透明度，30 到 100
    "overlay_align": "left",     # 靠左 left / 置中 center / 靠右 right
    "overlay_grow": "up",        # up = 最新的在最下面、舊的往上推；down = 從上面往下排
    "overlay_lines": 0,          # 最多同時顯示幾則，0 = 放得下就顯示
    # ---- 進階，介面上沒有 ----
    "interval": 0.7,             # 每隔幾秒看一次螢幕
    "ocr_scale": 1.5,            # 辨識前先把畫面放大，小字比較準
    "whisper_threads": 4,
    "threads": 0,                # 翻譯引擎用幾個 CPU 核心，0 = 自動
}

# 介面可以改的設定（其他的只能直接改檔案）
THEMES = ("dark", "light", "system")
ACCENTS = ("mint", "blue", "violet", "rose", "cyan")
EDITABLE = {"ui_lang", "theme", "accent", "my_lang", "their_lang", "their_auto", "transcript_original", "close_to_tray", "tone", "quality", "game_terms", "term_packs", "back_translate", "use_context",
            "voice_sensitivity", "end_silence", "speech_mode", "speech_device", "listen_apps", "regions", "overlay_form", "overlay_monitor", "drop_echo", "ptt", "ptt_key", "speaker_device", "speaker_name",
            "mic_device", "mic_name", "also_english", "on_top", "show_pronunciation", "copy_spoken", "setup_done", "hotkeys",
            "overlay", "overlay_font", "overlay_seconds", "overlay_backdrop", "overlay_original", "overlay_mine",
            "overlay_family", "overlay_bold", "overlay_color", "overlay_outline", "overlay_names", "overlay_name_colors",
            "overlay_opacity", "overlay_align", "overlay_grow", "overlay_lines"}
MAX_REGIONS = 6
MAX_LAYOUTS = 5
OVERLAY_FORMS = ("float", "bar")
OVERLAY_LOOK = tuple(k for k in DEFAULTS if k.startswith("overlay_")
                     and k not in ("overlay_rect", "overlay_form", "overlay_monitor", "overlay_layouts"))      # 「恢復預設外觀」會還原的項目


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Config:
    def __init__(self, folder: Path):
        self.path = folder / "config.json"
        self._lock = threading.RLock()
        self.error = ""              # 上一次存檔失敗的原因（空的：有存好）
        self.on_fail = None          # 存不進去時呼叫一次（傳原因），之後存好了再失敗才會再呼叫
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
        old = self.data.get("region")
        if isinstance(old, dict) and not self.data["regions"]:      # 舊版的單一聊天區搬成第一個區域
            self.data["regions"] = [{"id": "r1", "name": "", "on": True, "app": "", "anchor": None,
                                     **{k: int(old.get(k, 0)) for k in ("left", "top", "width", "height")}}]
        self.data["region"] = None
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

    def set(self, **changes) -> bool:
        """改設定並馬上寫進設定檔。回傳有沒有真的存好。"""
        with self._lock:
            self.data.update(changes)
            return self.save()

    def save(self) -> bool:
        with self._lock:
            failed_before, problem = bool(self.error), ""
            for attempt in range(3):      # 防毒軟體或同步軟體偶爾會把檔案鎖住一下子，多試兩次
                try:
                    _write_json(self.path, self.data)
                    self.error = ""
                    return True
                except OSError as ex:
                    problem = str(ex) or type(ex).__name__
                    time.sleep(0.05 * (attempt + 1))
            self.error = problem
        if not failed_before and self.on_fail:
            try:
                self.on_fail(problem)
            except Exception:  # noqa: BLE001
                pass
        return False


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

    def merge(self, items) -> int:
        """匯入：只加進原本沒有的詞（原本就有的不動），回傳加了幾個。"""
        added = 0
        with self._lock:
            have = {i["src"].lower() for i in self.items}
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                src, dst = str(item.get("src", "")).strip()[:80], str(item.get("dst", "")).strip()[:80]
                if src and dst and src.lower() not in have:
                    self.items.append({"src": src, "dst": dst})
                    have.add(src.lower())
                    added += 1
            if added:
                self._save()
        return added

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

    def merge(self, items, sources=(), targets=()) -> int:
        """匯入：只加進原本沒教過的句子（同一句、同一個目標語言算教過），回傳加了幾句。
        sources、targets：認得的原文語言和目標語言，別的不收。"""
        added = 0
        with self._lock:
            have = {(i["src"], i["target"]) for i in self.items}
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or len(self.items) >= self.LIMIT:
                    continue
                src, dst = str(item.get("src", "")).strip()[:500], str(item.get("dst", "")).strip()[:500]
                lang, target = str(item.get("lang", "")), str(item.get("target", ""))
                if not src or not dst or src == dst or lang not in sources or target not in targets or (src, target) in have:
                    continue
                self.items.append({"src": src, "dst": dst, "lang": lang, "target": target})
                have.add((src, target))
                added += 1
            if added:
                self._save()
        return added

    def _save(self) -> None:
        try:
            _write_json(self.path, self.items)
        except OSError:
            pass
