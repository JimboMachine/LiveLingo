"""設定檔和詞彙表（都存成 JSON，放在使用者的資料夾）。"""

import json
import locale
import os
import threading
import time
from pathlib import Path

UI_LANGS = ("zh-TW", "zh-CN", "en", "ko", "ja", "vi", "th", "id", "es", "pt", "ru", "de", "fr")      # 翻譯語言有幾種，介面就有幾種（有測試在看）

DEFAULTS = {
    "ui_lang": None,             # 介面語言；None = 第一次開啟時照 Windows 的語言決定
    "my_lang": "zh-TW",          # 我看得懂的語言
    "their_lang": "ko",          # 對方用的語言（開了自動偵測時：目前判斷到的語言）
    "their_auto": False,         # 自動偵測對方用的語言
    "auto_langs": [],            # 自動偵測時只在這幾種語言裡判斷（語音辨識的語言名：ko、zh、en…）；空的 = 全部。目前的語言和我的語言一定算在裡面
    "transcript_dir": "",        # 字幕記錄存在哪個資料夾（空的：文件\\LiveLingo）
    "transcript_original": True, # 字幕記錄裡同時留原文
    "close_to_tray": True,       # 關掉視窗時收到系統匣，不結束程式
    "seen_version": "",          # 使用者最後一次看過「這一版改了什麼」的版本；空的 = 還沒有紀錄（不是設定，匯出和恢復預設都不碰）
    "tray_hint_shown": False,    # 第一次收到系統匣時提醒過了沒
    "tone": "casual",            # casual = 朋友語氣（韓文半語）；polite = 有禮貌
    "quality": "auto",           # auto = 照顯示卡自動決定；standard；high
    "game_terms": True,          # 內建的遊戲用語表（英雄聯盟，韓文和中文之間）
    "term_packs": ["lol", "chat"],   # 用哪幾包內建用語（terms.PACKS）；game_terms 關掉時都不用
    "game_auto": True,           # 照正在玩的遊戲自動切換：遊戲專用的用語包（terms.GAME_PACKS）和遊戲專用詞只在那個遊戲開著時用
    "tone_keep": False,          # 保留語氣：髒話、玩笑、網路用語翻成對方語言裡一樣強烈的口語（關掉 = 直翻，比較收斂）
    "back_translate": True,      # 我說的、打的翻成對方的語言之後，再翻回來給我看（回譯）
    "use_context": True,         # 翻譯時參考前面兩句對話（代名詞、省略的主詞比較不會翻錯）
    "remember_works": True,
    "use_shown_translation": False,  # 畫面上是雙語字幕時，直接用畫面上我的語言那一行當翻譯（預設關：聊天室裡兩種語言混著時會配錯）          # 記住在看哪一部影片、哪一個網頁（讀取來源那個視窗的標題，只存在這台電腦）
    "remember_translations": True,   # 翻過的句子記在這台電腦上（memory.db），同一句再出現就直接用，不用重翻
    "use_shared": True,          # 使用匯入的翻譯檔（朋友翻過的）；關掉 = 每一句都自己翻
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
    "live_captions": True,       # 即時字幕：對方還在說，就先把目前聽到的顯示出來、先翻（整句說完再換成正式的）
    "remember_speakers": False,      # 記住取過名字的說話者：名字和聲紋存在這台電腦上（voices.json），下次開就認得。關掉會把存的刪掉
    "tell_speakers": False,      # 分辨不同的說話者（好幾個人在同一個語音頻道時，照聲音分成說話者 1、2、3）；第一次打開會下載一個小模型
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
    "copy_spoken": True,
    "learn_mode": False,         # 語言學習：字幕旁邊標出一兩個生字，可以收進單字本（learn.py）
    "recap_auto": True,          # 一段對話結束（安靜 10 分鐘）自動整理重點（recap.py）
    "speak_out": False,          # 我說的、打的翻成對方的語言之後，用 Windows 內建語音念出來（core/speak.py）
    "speak_device": None,        # 念到哪個播放裝置（裝置 id，例如 VB-CABLE 的 CABLE Input），None = Windows 預設
    "speak_name": "",            # 用哪個 Windows 語音念（語音的名字），空的 = 照語言自動選
    "update_check": True,        # 開啟時檢查有沒有新版本（關掉的話，模型都下載好之後 LiveLingo 完全不連網路）
    "setup_done": False,
    "history_keep": True,        # 對話歷史存在這台電腦上（history.jsonl）；關掉就不再記新的
    "stream": False,             # 直播模式：給 OBS 的「瀏覽器來源」一個固定網址，乾淨的雙語字幕（stream.py）
    "stream_original": True,     # 直播字幕：譯文上面也顯示原文
    "stream_mine": True,         # 直播字幕：我說的、打的也顯示
    "stream_port": 47860,        # 直播字幕的網址用哪個連接埠（被佔用時往後找）；進階，介面上沒有
    "stream_key": "",            # 直播字幕網址裡的通行碼：第一次打開直播模式時產生，之後固定（網址才不會每次變）
    "share_visible": True,       # 浮動字幕和主視窗要不要讓「分享螢幕、直播、錄影」看得到；關掉就只有自己看得到
    "engine_rest": 30,           # 幾分鐘沒翻譯就讓翻譯引擎休息（放掉記憶體和顯示卡記憶體），0 = 一直開著（service.ENGINE_REST）
    "history_days": 30,          # 歷史保留幾天（history.DAYS：7、30、90，0 = 不限）
    "usage_keep": True,          # 使用數字（每天幾句、失敗幾次、平均等多久）記在這台電腦上（usage.json）；只有數字
    "store_dir": "",             # 大檔案（模型、翻譯引擎）搬到哪個資料夾；空的 = 跟設定放一起（storage.py）
    "store_leftover": [],        # 搬完之後還刪不掉的舊資料夾（當時檔案還開著），下次開程式再刪
    # 全域快速鍵（hotkeys.ACTIONS）：開關字幕、開關麥克風、複製我上一句的翻譯、叫出快速輸入框、回話建議。空字串 = 不用
    "hotkeys": {"overlay": "Ctrl+Alt+S", "mic": "Ctrl+Alt+M", "copy": "Ctrl+Alt+C", "quick": "Ctrl+Alt+T", "reply": "Ctrl+Alt+R"},
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
EDITABLE = {"ui_lang", "theme", "accent", "my_lang", "their_lang", "their_auto", "auto_langs", "transcript_original", "close_to_tray", "tone", "tone_keep", "quality", "game_terms", "term_packs", "game_auto", "back_translate", "use_context",
            "remember_translations", "use_shared", "remember_works", "use_shown_translation",
            "voice_sensitivity", "end_silence", "speech_mode", "speech_device", "listen_apps", "regions", "overlay_form", "overlay_monitor", "drop_echo", "live_captions", "tell_speakers", "remember_speakers", "ptt", "ptt_key", "speaker_device", "speaker_name",
            "mic_device", "mic_name", "also_english", "on_top", "show_pronunciation", "copy_spoken", "speak_out", "speak_device", "speak_name", "learn_mode", "recap_auto", "update_check", "setup_done", "hotkeys", "history_keep", "history_days", "usage_keep", "engine_rest", "share_visible", "stream", "stream_original", "stream_mine",
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
    for code in ("vi", "th", "id", "es", "pt", "ru", "de", "fr"):
        if name.startswith(code):
            return code
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
    """我的詞彙表：[{"src": 看到這個詞, "dst": 要翻成, "game": 只在這個遊戲用（程式檔名小寫，可以沒有）}]。
    有 game 的是那個遊戲的專用詞：只有在玩那個遊戲（terms.find_game 認出來）的時候才用。"""

    def __init__(self, folder: Path):
        self.path = folder / "glossary.json"
        self._lock = threading.RLock()
        self.items: list[dict] = [dict(i) for i in GLOSSARY_SEED]   # 第一次使用時先放幾個例子
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [self._clean(i) for i in raw if isinstance(i, dict)
                          and str(i.get("src", "")).strip() and str(i.get("dst", "")).strip()]
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            self.items = []

    @staticmethod
    def _clean(item: dict) -> dict:
        out = {"src": str(item["src"]).strip()[:80], "dst": str(item["dst"]).strip()[:80]}
        game = str(item.get("game") or "").strip().lower()[:80]
        if game:
            out["game"] = game
        return out

    def as_dict(self, game: str = "") -> dict[str, str]:
        """現在要用的詞：通用的，加上現在這個遊戲的專用詞（同一個詞兩邊都有時，遊戲專用的優先）。"""
        game = (game or "").lower()
        with self._lock:
            out = {i["src"]: i["dst"] for i in self.items if not i.get("game")}
            if game:
                out.update({i["src"]: i["dst"] for i in self.items if i.get("game") == game})
            return out

    def games(self) -> set[str]:
        """有專用詞的遊戲（程式檔名小寫）。"""
        with self._lock:
            return {i["game"] for i in self.items if i.get("game")}

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(i) for i in self.items]

    def add(self, src: str, dst: str, game: str = "") -> bool:
        src, dst, game = src.strip()[:80], dst.strip()[:80], (game or "").strip().lower()[:80]
        if not src or not dst:
            return False
        with self._lock:
            self.items = [i for i in self.items if i["src"].lower() != src.lower() or i.get("game", "") != game]
            self.items.insert(0, {"src": src, "dst": dst, **({"game": game} if game else {})})
            self._save()
        return True

    def remove(self, src: str, game: str = "") -> None:
        game = (game or "").lower()
        with self._lock:
            self.items = [i for i in self.items if i["src"] != src or i.get("game", "") != game]
            self._save()

    def merge(self, items) -> int:
        """匯入：只加進原本沒有的詞（原本就有的不動），回傳加了幾個。遊戲專用詞照樣是那個遊戲的。"""
        added = 0
        with self._lock:
            have = {(i["src"].lower(), i.get("game", "")) for i in self.items}
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or not str(item.get("src", "")).strip() or not str(item.get("dst", "")).strip():
                    continue
                clean = self._clean(item)
                key = (clean["src"].lower(), clean.get("game", ""))
                if key not in have:
                    self.items.append(clean)
                    have.add(key)
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
