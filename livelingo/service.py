"""
程式的主體：管理翻譯引擎、三種來源（讀螢幕、聽對方說話、聽我說話）、詞彙表和設定，
把發生的事情用事件送給介面。介面只是顯示和按按鈕，所有工作都在這裡做。
"""

import itertools
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from . import __version__, apps, assets, changes, chatpaint, filesubs, gpu, guide, mention, oswin, paths, report, snap, storage, subtitles, updater, voicebook, works
from .config import (ACCENTS, DEFAULTS, EDITABLE, HOTKEYS_V, MAX_LAYOUTS, MAX_REGIONS, OVERLAY_FORMS, OVERLAY_LOOK, THEMES, UI_LANGS,
                     Config, Glossary, Lessons, migrate_hotkeys)
from .core.engine import Engine
from .core.romanize import romanize
from .core.speak import Speaker
from .core.translate import LANGS, MAX_TEXT, Stop, Stopped, Translator, detect_language, numbers_lost, similarity, why
from .history import DAYS as HISTORY_DAYS
from .jobs import Jobs
from .memory import KIND as MEMORY_KIND
from .memory import RANGES as MEMORY_RANGES
from .memory import Memory
from . import names as keepwords
from .names import NameWatch
from .qualitycheck import QualityCheck
from . import textfix
from .corrections import Corrections
from . import cheatsheet, gametools, imagetext
from . import introduce, pace, sessionlog, study, tone as tone_mod, voicecmd
from . import gamepad as gamepad_mod
from .album import Album
from .storybook import StoryBook
from .history import History
from .learn import LEVELS as LEARN_LEVELS
from .learn import Wordbook, family_of
from .recap import GAP as RECAP_GAP
from .recap import Recaps, sessions, worth
from .hotkeys import ACTIONS as HOTKEY_ACTIONS
from .hotkeys import NOTES as HOTKEY_NOTES
from .hotkeys import Hotkeys
from .hotkeys import normalize as hotkey_normalize
from .inplace import InPlace
from .overlay import BAR_LOOK, Overlay
from .phrases import Phrasebook
from .paths import self_command
from .terms import PACKS, TermPack, find_game, game_name, packs_for
from .stream import StreamOut
from .transcript import Transcript
from .tray import Tray
from .usage import Usage

VOICE_KIND = {"listen": "loopback", "mic": "mic"}
SOURCE_OF = {"loopback": "listen", "mic": "mic"}
ALPHABETIC = ("en", "vi", "id", "es", "pt", "ru", "de", "fr")      # 一個字母不是一個字的語言：一句話要比較多字母才算夠長
VOICE_FAMILIES = ("ko", "zh", "en", "ja", "vi", "th", "id", "es", "pt", "ru", "de", "fr")      # 語音辨識認得、我們也翻得了的語言
# 自動偵測對方的語言：語音辨識對短句、背景音樂、遊戲音效常常猜錯是哪種語言（Jimbo 的紀錄：一天換了六十幾次，
# 多半是一句英文被當成韓文或日文，每換一次還要重開讀螢幕和麥克風）。所以一句話不算數：
SWITCH_RUN = 3                 # 連續幾句都像「同一個別的語言」，才把對方的語言換過去
SWITCH_RUN_GAP = 120.0         # 這一串句子之間隔了這麼久（秒）就重新算
SWITCH_COOLDOWN = 30.0         # 剛換過去之後，這麼久（秒）內不再換
EVIDENCE_MIN = {True: 8, False: 3}      # 一句話至少幾個字（字母語言 8 個字母，其他 3 個字）才看得出是什麼語言；更短的不算也不打斷


# 「靈敏度」1 到 5 各是多少的語音偵測門檻（越低越容易把聲音當成說話）
VAD_THRESHOLD = {1: 0.7, 2: 0.6, 3: 0.5, 4: 0.4, 5: 0.3}
MIC_HEARD_LEVEL = 0.3      # 音量條（speech.level_of）到這麼高，才算麥克風真的收到了聲音（約 -45 dB；安靜房間的底噪不會到）
PARTIAL_EVERY = 0.08      # 譯文邊翻邊顯示：最快多久更新一次畫面（秒）
VOICES_SAVE_EVERY = 30.0      # 記住的聲音：聲紋跟著新的句子變了之後，最快多久存一次（秒）
INSTALLER_RETRY = (20.0, 60.0, 300.0)      # 剛更新完刪不掉的安裝檔（安裝程式還沒結束）：隔這麼久再試（秒）
RECONNECT_WAITS = (2, 4, 8, 15, 30, 30, 30)      # 收音裝置斷線後，隔幾秒再試一次；全部試完（大約兩分鐘）還不行才放棄
BEAMS = {"fast": 1, "accurate": 5}      # 語音辨識「比較快／比較準」各用多寬的搜尋
SETTINGS_KIND = "livelingo-settings"      # 匯出檔裡用來認「這是 LiveLingo 的設定檔」
# 不屬於「設定」的東西：視窗大小位置、第一次設定做過了沒、哪些來源現在開著、大檔案搬去哪。匯出、匯入、恢復預設都不碰
SETTINGS_SKIP = ("window", "layout", "hotkeys_v", "setup_done", "tray_hint_shown", "seen_version", "region", "sources", "store_dir", "store_leftover", "stream_key")
# 恢復預設時留著不動的：語言、框好的聊天區、自己存的版面、字幕資料夾（這些是花力氣弄好的，不是「調壞了的設定」）
RESET_KEEP = ("ui_lang", "my_lang", "their_lang", "regions", "overlay_layouts", "transcript_dir", "keep_words", "name_skip", "abbr_skip", "game_profiles", "player_notes")
MAX_PINS = 3                                   # 浮動字幕最上面最多釘幾句（2.17.0）
KEYS_SECONDS = 10.0                            # 快捷鍵一覽小卡停幾秒
CHAT_OPEN_KEYS = ("enter", "slash", "t", "none")      # 說話送進遊戲聊天：先按哪個鍵打開聊天框
EAR_SPLITS = ("off", "left", "right")           # 左右耳分流（2.18.0）：念對方的話只放在哪一邊
MAX_MARKS = 200                                 # 一場最多記幾個精華標記
SPEED_SECONDS = 5.0                             # 自動選語音設定：拿幾秒的聲音測速度


def pick_speech(speed: float) -> tuple[str, bool]:
    """辨識 1 秒聲音要 speed 秒 →（speech_mode, live_captions）。
    很快（顯示卡、新的處理器）：用「比較準」也來得及；普通：比較快＋即時字幕；很慢：即時字幕會搶走整句的時間，關掉。"""
    if speed <= 0.12:
        return "accurate", True
    if speed <= 0.45:
        return "fast", True
    return "fast", False
ADVANCED_RANGE = {"interval": (0.2, 5.0), "ocr_scale": (1.0, 3.0), "whisper_threads": (1, 32), "threads": (0, 64),
                  "stream_port": (1024, 65000)}
LIBRARY_KIND = "livelingo-library"      # 匯出檔裡用來認「這是 LiveLingo 的詞彙檔」


REVIVE_TIMES = 3            # 翻譯引擎自己停掉時，最多自動重開幾次……
REVIVE_WINDOW = 600.0       # ……在這麼多秒之內；超過就不再試
WHATS_NEW_FLOOR = "2.5.0"      # 沒有「上次看到哪一版」的紀錄（這個功能出現以前就裝好的）：把這一版以後錯過的改動一次說完
REMIND_EVERY = 1800        # 每日複習提醒：多久看一次有沒有該複習的字（2.19.0）
MAX_MARKED = 60            # 最近幾則訊息的生字標記留著（介面重開也看得到）
RECAP_RECENT = 6 * 3600     # 自動摘要只補最近這麼久之內結束的對話
RECAP_EVERY = 30.0          # 每隔幾秒看一次有沒有剛結束、還沒摘要的對話
# 首頁的情境卡（2.14.0）：名字 → (要改的設定, 要開或關的來源)。只碰顯示方式和來源，不碰語言、裝置、框好的區域
SCENARIOS = {
    "game": ({"overlay": True, "overlay_form": "float", "live_captions": True}, {"screen": True, "listen": True, "mic": False}),
    "video": ({"overlay": True, "overlay_form": "bar", "live_captions": True}, {"screen": False, "listen": True, "mic": False}),
    "call": ({"overlay": True, "overlay_form": "float", "drop_echo": True}, {"screen": False, "listen": True, "mic": True}),
    "stream": ({"overlay": True, "stream": True, "live_captions": True}, {"screen": False, "listen": True, "mic": True}),
}
GAME_EVERY = 5.0            # 每隔幾秒看一次現在在玩哪個遊戲（遊戲用語自動切換）
UPDATE_RECHECK = 4 * 3600   # 程式一直開著（縮在系統匣好幾天）的時候，每隔幾個小時再看一次有沒有新版本
ENGINE_REST = (0, 10, 30, 60)    # 設定「幾分鐘沒翻譯就讓翻譯引擎休息」可以選的值，0 = 一直開著
SPEECH_REST = 120.0         # 兩邊收音都關掉這麼多秒：把語音辨識模型（約 1 GB）放掉，下次開收音再載入


class EventHub:
    """把事件送給所有開著的介面（通常只有一個視窗）。"""

    def __init__(self):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self.ever_connected = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.add(q)
            self.ever_connected = True
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: str, data) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass


class RealAudio:
    """真的音訊裝置。測試時會換成假的。ways：記住哪支麥克風要直接用第二種收音方式（core/audio.py 的 Ways）。"""

    def __init__(self, ways=None):
        self.ways = ways

    def devices(self) -> dict:
        from .core.audio import _soundcard

        sc = _soundcard()

        def rows(items, default_id):
            return [{"id": d.id, "name": d.name, "default": d.id == default_id} for d in items]

        try:
            spk_default = sc.default_speaker().id
        except Exception:  # noqa: BLE001
            spk_default = None
        try:
            mic_default = sc.default_microphone().id
        except Exception:  # noqa: BLE001
            mic_default = None
        return {"speakers": rows(sc.all_speakers(), spk_default),
                "microphones": rows(sc.all_microphones(), mic_default)}

    def capture(self, kind: str, device, on_error=None, on_open=None):
        from .core.audio import Capture

        return Capture(kind, on_error=on_error, device=device, on_open=on_open, ways=self.ways)

    def apps_available(self) -> bool:
        from .core import procaudio

        return procaudio.available()

    def capture_apps(self, exes: list[str], on_error=None, on_open=None):
        """只收這幾個程式的聲音。這台電腦沒有這個功能的話回傳 None。"""
        from . import apps
        from .core import procaudio

        if not procaudio.available():
            return None
        return procaudio.AppCapture(exes, apps.pids_of, on_error=on_error, on_open=on_open)


class Service:
    def __init__(self, data: Path, hub: EventHub | None = None, audio=None):
        self.data = data
        self.hub = hub or EventHub()
        self.cfg = Config(data)
        self.cfg.on_fail = lambda error: self.notice("save_failed", error=error)      # 設定檔寫不進去要讓使用者知道
        self.changes = changes.bundled()
        self.updated_from = updater.just_updated(data)
        if self.updated_from:
            self.log(f"updated from {self.updated_from} to {__version__}")
        self.whats_new = self._whats_new()
        self.store = storage.store_dir(data, self.cfg["store_dir"])      # 大檔案（模型、翻譯引擎）放的地方，可以搬到別的磁碟
        self._move: dict | None = None      # 正在搬大檔案：{"to", "done", "total"}
        self._move_cancel = threading.Event()
        self._move_lock = threading.Lock()
        self.glossary = Glossary(data)
        self.lessons = Lessons(data)      # 使用者教過的整句翻法
        self.memory = Memory(data)        # 翻過的句子：同一句再出現就直接用；也可以匯出給朋友、匯入朋友的
        self.memory.mine_on, self.memory.shared_on = bool(self.cfg["remember_translations"]), bool(self.cfg["use_shared"])
        self._memory_told = 0.0
        self.game = ""                 # 現在在玩的遊戲（程式檔名小寫，terms.find_game），空的 = 沒認出來
        self._work_seen = (0.0, "", "", "")      # (什麼時候看的, via, place, work key)：視窗標題最多兩秒看一次
        self._work_told: set[str] = set()        # 這次開程式已經提示過「這部翻過了」的作品
        self.terms = TermPack(packs_for(self.cfg["term_packs"], self.game, bool(self.cfg["game_auto"])))
        from .core import audio as audio_core

        self.audio = audio or RealAudio(audio_core.Ways(self.data / "mic_ways.json", __version__))      # 哪支麥克風要直接用第二種收音方式
        audio_core.log = self.log      # 收音出錯的細節寫進紀錄檔
        self.gpu = {"name": None, "vram_mb": 0, "tier": "cpu"}
        self.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        self.fallback = False          # 高品質開不起來，自動退回標準
        self.engine: Engine | None = None
        self.translator = Translator("http://127.0.0.1:8177", glossary=self._words_now,
                                     tone=self.cfg["tone"], hints=self._hints, lessons=self.lessons.list,
                                     memory=self.memory)
        self.translator.keep_tone = bool(self.cfg["tone_keep"])
        self.translator.keep = lambda: list(self.cfg["keep_words"])
        self.name_watch = NameWatch()          # 名字建議（2.14.0）：數對方一直提到的遊戲 ID
        self.abbr_watch = textfix.abbr_watch()     # 看不懂的縮寫提醒（2.15.0）
        self.corrections = Corrections(self.data)  # 改正紀錄，每週錯誤報告用（2.15.0）
        self.storybook = StoryBook(self.data)       # 劇情書（2.16.0）
        self.album = Album(self.data)               # 框選翻譯相簿（2.16.0）
        self._profile_game = ""                     # 遊戲專屬版面：上一次套用的是哪個遊戲
        self._fullscreen_told = False               # 獨佔全螢幕提醒：這次開遊戲說過了
        self.pins: list[int] = []                   # 釘在浮動字幕最上面的句子（2.17.0，最多 MAX_PINS 句）
        self.gamepad = gamepad_mod.Gamepad(self._pad_action)      # 手把快捷鍵（2.17.0，預設關）
        self.marks: list[dict] = []                 # 精華標記（2.18.0）：這一場按下標記的時間點
        self.voice_paused = False                   # 語音指令「暫停翻譯」（2.18.0）：自動翻譯先停，打字照常
        self.pace = pace.PaceWatch()                # 對方講太快的提醒（2.18.0）
        self._intro_asked: set = set()              # 聽到自我介紹：這次已經問過的說話者
        self._msg_len: dict[int, float] = {}        # 每一則語音說了幾秒（存成字幕檔時算開始時間用）
        self._speed_testing = False
        self.quality = QualityCheck(self.data, on_change=lambda st: self.hub.publish("quality", st), log=self.log)
        self.private = False              # 隱私暫停（2.14.0）：開著的時候翻譯照常，但什麼紀錄都不留（不存設定檔，重開就回到平常）
        self._auto_started: list[str] = []    # 開遊戲就開始翻：是我（不是使用者）打開的來源，遊戲關掉時只關這些
        self._auto_apps: set[str] = set()     # 上一次看到開著的、會觸發自動開始的程式
        # 翻譯引擎和語音辨識都用處理器的電腦上，兩個輪流跑（見 _cpu_turn）
        self._cpu_lock = threading.Lock()
        self.translator.turn = self._cpu_turn
        self._terms_changed()
        self.phase = "starting"        # starting / updating / need_download / downloading / loading / ready / error
        self.detail: dict = {}
        # quiet：麥克風開著卻完全沒聲音時是 "silent"，Windows 的隱私權設定擋住時是 "blocked"
        # lost：收音裝置用到一半斷線（耳機拔掉、藍牙斷了），正在自動重新連接
        self.sources = {k: {"on": False, "busy": False, "error": "", "device": "", "speaking": False, "quiet": "",
                            "apps": False, "lost": False}
                        for k in ("screen", "listen", "mic")}
        self._vu = {"listen": 0.0, "mic": 0.0}
        self._vu_sent = 0.0
        self._finding_mic = False
        self._mic_heard = False      # 這一次收音開始之後，麥克風有沒有送來過像說話的聲音（有的話，之後的安靜就只是沒人在說話）
        self._find_cancel = threading.Event()
        self._update_choices: queue.Queue = queue.Queue()      # 使用者對「有新版本」的回答
        self._update_stop = threading.Event()
        self.update_offer: dict | None = None     # 程式開著的時候查到的新版本：{"version", "size", "notes"}（畫面上小小提醒，使用者按了才更新）
        self._update_info: dict | None = None     # 同一個新版本的下載資料（網址、核對碼）
        self._update_declined = ""                # 使用者說「這次先不要」的那一版：這次開著的時候不再提醒
        self.messages: deque[dict] = deque(maxlen=200)
        self.jobs = Jobs()             # 翻譯的排隊：我自己的句子先翻，螢幕上讀到的排太久就略過（jobs.py）
        self._skipped = 0              # 略過了幾則還沒告訴使用者
        self._skip_told = 0.0
        self._engine_ok = threading.Event()      # 沒亮 = 翻譯引擎掛了，正在自動重開（翻譯先等它）
        self._engine_ok.set()
        self._revive_lock = threading.Lock()
        # 翻譯引擎休息：太久沒翻譯就把它關掉（放掉電腦記憶體和顯示卡記憶體），下一句來了再叫醒
        self._resting = False
        self._last_use = time.monotonic()
        self._voice_off_since: float | None = time.monotonic()      # 兩邊收音都關著是從什麼時候開始（None = 有開著）
        self._revivals: list[float] = []         # 最近幾次自動重開的時間
        self._retries: dict[int, int] = {}       # 引擎掛掉時正在翻的句子：補翻過幾次
        self._error_told = 0.0                   # 上一次在畫面上說「有一部分出了錯」的時間
        self.window = None             # 視窗控制（window.py 會放進來）
        self.main_hwnd = 0             # 主視窗的 Windows 視窗代碼（window.py 開好視窗後放進來）
        self.can_window = False        # 視窗可以由程式調整大小、置頂（用瀏覽器開的時候不行）
        self.view = "main"
        self.watcher = None
        self.ocr = None
        self.recognizer = None
        self.pipelines: dict = {}
        self.meters = None
        self.chrome = ("#171A1E", "#F2F4F6", True)      # 標題列的顏色（底色、文字、是不是深色）
        self.closing = threading.Event()
        self._ids = itertools.count(1)
        self.transcript = Transcript()
        self.history = History(self.data, bool(self.cfg["history_keep"]), int(self.cfg["history_days"]))
        self.wordbook = Wordbook(self.data)      # 語言學習的單字本（learn.py）
        self.recaps = Recaps(self.data)          # 對話摘要（recap.py）
        self._recap = {"state": "off", "id": ""}      # 正在整理的摘要（給介面）
        self._recap_checked = 0.0
        self._all_terms = None      # 查生字意思用的全部用語包（第一次用到才讀）
        self._words: dict[int, list[dict]] = {}      # 訊息 id → 標出來的生字 [{w, lang, meaning}]（只留最近的）
        self._explained: dict[int, str] = {}      # 訊息 id → 「為什麼這樣說」的說明（2.19.0，只留最近的）
        self.usage = Usage(self.data, bool(self.cfg["usage_keep"]))      # 每天幾句、失敗幾次、平均等多久（只有數字）
        self._counted: set[int] = set()      # 已經算進使用數字的訊息（重翻不再算）
        self.tray = Tray(self)
        self.hotkeys = Hotkeys(self._hotkey, on_state=lambda states: self.hub.publish("hotkeys", self.hotkeys_state()))
        self._quick = None             # 快速輸入框開著的時候是那個小程式
        self._reply_child = None       # 回話小框開著的時候是那個小程式
        self.phrases = Phrasebook(self.data)      # 常用句庫（phrases.py）
        self._phrase_child = None      # 常用句庫小框開著的時候是那個小程式
        self._phrase_filling = False   # 正在背景補翻常用句
        self._phrase_lock = threading.Lock()
        self._card_child = None        # 翻譯結果小卡開著的時候是那個小程式
        self._card_gate = threading.RLock()      # 送去翻譯和登記小卡之間不讓翻好的結果插進來
        self._cards: dict[int, dict] = {}        # 訊息編號 → 在等它的那張小卡
        self._snap_busy = False        # 框選翻譯進行中
        self._alerted: deque = deque(maxlen=200)      # 已經響過提醒的訊息編號
        self._alert_at = 0.0
        self._replies: dict = {"id": 0, "state": "off", "items": []}      # 最近一次的回話建議（給介面）
        self._their_run = self._new_run()      # 自動偵測：現在連續幾句都像哪個別的語言（還不夠確定）
        self._their_switched_at = float("-inf")      # 自動偵測上一次換過去的時間（time.monotonic）
        self._recent_voice: deque[dict] = deque(maxlen=12)      # 最近聽到的句子（兩邊都記），用來認出麥克風收到的喇叭聲音
        self.delays: deque[tuple[float, float]] = deque(maxlen=10)      # 最近幾句語音：（辨識秒數, 翻譯秒數）
        # 語音辨識用顯示卡：state = off（沒選）/ need（要先下載）/ downloading / ready / failed（試過不行，已改用處理器）
        self.speech_gpu = {"state": "off", "percent": 0, "error": "", "mb": assets.CUDNN_MB, "active": False}
        self._speech_gpu_cancel = threading.Event()
        self._apps_audio_off = False      # 「只聽指定程式」這次試過不行，先改聽整台電腦（換選擇或重開程式會再試）
        self._voice_open: dict[str, bool] = {}      # 這個來源的收音裝置這一次有沒有成功打開過
        self._voice_tries: dict[str, int] = {}      # 斷線之後已經重試了幾次
        self._live: dict[str, dict] = {}      # 收音來源（loopback / mic）→ 還在說、先顯示出來的那一則（即時字幕）
        self._live_stop: dict[str, Stop] = {}      # 收音來源 → 正在翻的那半句的「停」按鈕
        # 分辨說話者：聲紋模型和分出來的人只在記憶體裡（core/voices.py）；名字是使用者取的。
        # 有開「記住取過名字的人」的話，取過名字的人另外存在 voices.json（voicebook.py），下次開就認得
        self._voices_saved = 0.0      # 上一次存「記住的聲音」是什麼時候
        self.voices = None
        self.speakers = {"state": "off", "percent": 0, "error": "", "mb": assets.VOICE_MB}
        self._speaker_names: dict[int, str] = {}
        # 每個說話者自己的語言：{編號: (主語言, 副語言)}，語言代碼跟 their_lang 一樣（"" = 沒設）。
        # 設了的人，他說的話只在這兩個語言裡聽（主語言優先），不跟著「對方的語言」和自動偵測走
        self._speaker_langs: dict[int, tuple[str, str]] = {}
        self._speaker_run = 1      # 按一次「全部重新分辨」加一：之前標的編號就不算數了
        self._black_regions: set[str] = set()      # 抓到的畫面是全黑的聊天區（遊戲用全螢幕時會這樣）
        self._lock = threading.RLock()
        self.started = time.time()
        self._report = ""             # 上一次做給使用者看的問題報告（存檔、複製用的就是這一份）
        self._download_cancel = threading.Event()
        self._selecting = False
        self._subs_job: filesubs.Job | None = None      # 影片字幕：現在做的那一個（filesubs.py）
        self._subs_result: dict | None = None
        self._subs_key = ""
        self.on_quit = lambda: None    # 請視窗關閉、讓程式正常結束（介面按離開）
        self.exit_now = lambda: self.on_quit()   # 立刻結束整個程式（更新時用；__main__ 會換成真的）
        self.overlay = Overlay(self)   # 浮動留言
        # 原地覆蓋翻譯（inplace.py）：譯文蓋在畫面上原文的位置；視窗跟浮動留言用同一種，但擷取拍不到
        self.inplace = InPlace(self._inplace_translate, self._inplace_wanted, self.overlay.window_class,
                               target=lambda: self.cfg["my_lang"], busy=lambda: self.jobs.waiting() > 0, log=self.log)
        self.stream = StreamOut(self)  # 直播模式：給 OBS 的字幕頁（stream.py）
        # 念出我的譯文：Windows 內建語音念到選的播放裝置（core/speak.py）
        self.speaker = Speaker(self.data, device=lambda: self.cfg["speak_device"], on_done=self._spoke, log=self.log)
        # 念對方的話給我聽（2.14.0）：另一條隊伍、念到我自己的耳機，跟「念我的譯文給對方」分開排隊、分開選裝置
        self.reader = Speaker(self.data, device=lambda: self.cfg["speak_heard_device"], on_done=self._read_done, log=self.log,
                              voice=self.speaker.voice, ear=lambda: self.cfg["ear_split"])      # 左右耳分流（2.18.0）
        self._speak: dict = {"voice": "", "lang": "", "error": ""}

    # ------------------------------------------------------------------ 給介面的狀態

    def log(self, text: str) -> None:
        try:
            path = self.data / "app.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.unlink()
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
        except OSError:
            pass

    def watch_errors(self) -> None:
        """沒有人接住的錯誤（不管在哪一條執行緒）：寫進紀錄檔，並在畫面上說一聲，不要無聲無息地壞掉。"""
        def caught(kind, value, tb, where: str = "") -> None:
            if issubclass(kind, (KeyboardInterrupt, SystemExit)):
                return
            self.log(f"unhandled error{where}:\n" + "".join(traceback.format_exception(kind, value, tb)).rstrip())
            if time.time() - self._error_told > 60:      # 連續出錯的時候一分鐘說一次就好
                self._error_told = time.time()
                self.notice("part_failed")

        sys.excepthook = caught
        threading.excepthook = lambda args: caught(args.exc_type, args.exc_value, args.exc_traceback,
                                                   f" in thread {args.thread.name if args.thread else '?'}")

    def state(self) -> dict:
        return {
            "version": __version__,
            "config": self.cfg.snapshot(),
            "phase": self.phase,
            "detail": self.detail,
            "gpu": self.gpu,
            "mode": self.mode,
            "delay": self.delay(),
            "screen_stats": self.watcher.stats() if self.watcher else {},
            "speech_gpu": self.speech_gpu_state(),
            "speakers": self.speakers_state(),
            "ptt_keys": list(oswin.PTT_KEYS),
            "fallback": self.fallback,
            "sources": self.sources,
            "messages": list(self.messages),
            "glossary": self.glossary.list(),
            "game": self.game_state(),
            "lessons": self.lessons.list(),
            "memory": self.memory.state(),
            "langs": {k: v["family"] for k, v in LANGS.items()},
            "ui_langs": list(UI_LANGS),
            "can_window": bool(self.window or self.can_window),
            "view": self.view,
            "overlay": self.overlay.state(),
            "stream": self.stream.state(),
            "speak": self.speak_state(),
            "private": self.private,
            "quality": self.quality.state(),
            "corrections": self.corrections.report(),
            "album": self.album.list()[:60],
            "templates": sorted(gametools.templates()),
            "pins": list(self.pins),
            "marks": list(self.marks),
            "voice_paused": self.voice_paused,
            "voice_cmds": {lang: voicecmd.examples(lang) for lang in voicecmd.PHRASES},
            "gamepad": {"available": self.gamepad.available, "buttons": list(gamepad_mod.BUTTONS), "defaults": dict(gamepad_mod.DEFAULTS)},
            "scenarios": self.scenarios_state(),
            "replies": dict(self._replies),
            "words": {str(k): v for k, v in self._words.items()},
            "wordbook": self.wordbook.state(),
            "phrases": self.phrases_state(),
            "filesubs": self.filesubs_state(),
            "recaps": dict(self.recaps.state(), **self._recap),
            "record": self.record_state(),
            "hotkeys": self.hotkeys_state(),
            "history": self.history.state(),
            "settings_backup": self.settings_backup(),
            "changes": self.changes,                 # 每一版改了什麼（關於頁）
            "whats_new": self.whats_new,             # 更新後第一次開要說的：[{"version", "items"}]，新的在前；空的 = 不用說
            "updated_from": self.updated_from,       # 剛自動更新（或退回）完第一次開：之前的版本號（只說一次）
            "update": self.update_state(),
            "update_offer": self.update_offer or {},      # 程式開著的時候查到的新版本（沒有就是空的）
        }

    def set_phase(self, phase: str, **detail) -> None:
        self.phase, self.detail = phase, detail
        self.hub.publish("phase", {"phase": phase, "detail": detail, "mode": self.mode, "gpu": self.gpu,
                                   "fallback": self.fallback})

    def _source(self, key: str, **changes) -> None:
        self.sources[key].update(changes)
        self.hub.publish("source", {"key": key, **self.sources[key]})

    def notice(self, code: str, **params) -> None:
        """在介面上跳一則短訊息（代碼由介面翻成使用者選的語言）。"""
        self.hub.publish("notice", {"code": code, **params})

    def overlay_preview(self) -> bytes:
        """浮動留言照現在的設定會長什麼樣子（PNG）。"""
        import io

        out = io.BytesIO()
        cfg = self.cfg.snapshot()
        if cfg["overlay_form"] == "bar":      # 字幕條固定置中、最多兩句，預覽也要長那樣
            cfg.update(BAR_LOOK)
        chatpaint.preview(cfg).save(out, "PNG")
        return out.getvalue()

    def stored(self, what: str, ok: bool = True) -> None:
        """不是在設定畫面上直接改的東西（框選的範圍、浮動留言的位置、字幕資料夾）存好了：讓介面顯示「已儲存」。"""
        if ok:
            self.hub.publish("saved", {"what": what})

    # ------------------------------------------------------------------ 啟動

    def start(self) -> None:
        threading.Thread(target=self._translate_worker, daemon=True, name="translate").start()
        threading.Thread(target=self._engine_guard, daemon=True, name="engine-guard").start()
        threading.Thread(target=self._boot, daemon=True, name="boot").start()
        threading.Thread(target=self._game_watch, daemon=True, name="game-watch").start()
        threading.Thread(target=self._update_watch, daemon=True, name="update-watch").start()
        threading.Thread(target=self._review_remind, daemon=True, name="review-remind").start()

    def _boot(self) -> None:
        try:
            self._installers_cleanup()
            if self._update_if_needed():
                return
            self.overlay.apply()
            self.stream.apply()
            self.start_hotkeys()
            self._apply_gamepad()
            self._storage_cleanup()
            self.gpu = gpu.detect()
            self._speech_engine()      # 先把「語音辨識用顯示卡」現在的狀態算好，介面才知道要不要顯示下載
            self.speakers_load()       # 有開「分辨說話者」：聲紋模型先準備好，設定頁一打開就看得到記住的人
            self._prepare()
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self.set_phase("error", message=str(ex), log=str(self.data / "app.log"))

    def _whats_new(self) -> list[dict]:
        """
        更新或重新安裝之後第一次開：這一版和中間跳過的每一版改了什麼（用程式裡那份 changes.json，不用上網），
        使用者按了「知道了」才算看過。第一次安裝的人不用看；退回舊版也不用。
        """
        seen = str(self.cfg["seen_version"] or "")
        if seen == __version__:
            return []
        if not seen and not self.cfg["setup_done"]:      # 全新安裝：第一次設定的畫面就是說明
            self.cfg.set(seen_version=__version__)
            return []
        previous = seen or self.updated_from or ""
        try:
            if not seen:      # 沒有紀錄：不知道他上次看到哪一版，往回多說一點，免得漏掉
                previous = min((previous or WHATS_NEW_FLOOR), WHATS_NEW_FLOOR, key=changes._key)
            current = changes._key(__version__)
            out = [e for e in changes.since(self.changes, previous) if changes._key(e["version"]) <= current]
        except ValueError:
            out = []
        if not out:
            self.cfg.set(seen_version=__version__)      # 退回舊版、或沒有說明可說
        return out

    def whats_new_seen(self) -> None:
        """使用者按了「知道了」。"""
        self.whats_new = []
        self.cfg.set(seen_version=__version__)
        self.hub.publish("config", self.cfg.snapshot())

    def update_choice(self, choice: str) -> None:
        """介面上按了「現在更新」（now）、「這次先不要」（later）或下載中的「取消」（cancel）。
        開啟時的詢問在等答案；程式開著的時候查到新版本、畫面上小小提醒著的那一種，答案由這裡直接處理。"""
        if choice not in ("now", "later", "cancel"):
            return
        if self.update_offer and self.phase == "ready":
            if choice == "now" and self._update_info and not self._move:
                self._update_stop.clear()
                threading.Thread(target=self._update_now, daemon=True, name="update-now").start()
            elif choice == "later":
                self._update_declined = self.update_offer["version"]
                self.log(f"update to {self._update_declined} postponed by the user (reminder while the app was open)")
                self._offer(None)
            return
        self._update_choices.put(choice)
        if choice != "now":
            self._update_stop.set()

    def _offer(self, offer: dict | None, info: dict | None = None) -> None:
        """畫面上「有新版本」的提醒換成這一版（None = 收起來）。"""
        self.update_offer, self._update_info = offer, (info if offer else None)
        self.hub.publish("update_offer", offer or {})

    def _update_watch(self) -> None:
        """程式一直開著不關（縮在系統匣）的時候，開啟時查過之後就沒人再看了：每隔幾小時再看一次，有新版本就在畫面上提醒。"""
        while not self.closing.wait(UPDATE_RECHECK):
            try:
                self.update_recheck()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

    def update_recheck(self) -> bool:
        """查一次有沒有新版本；有（而且還沒提醒過、也不是使用者退回來或說先不要的那一版）就在畫面上提醒，回傳 True。
        只提醒，不會自己下載或更新；使用者在設定裡關掉了檢查就不連網路。"""
        if not updater.enabled() or not self.cfg["update_check"] or self.phase != "ready":
            return False
        try:
            info = updater.check()
        except Exception as ex:  # noqa: BLE001  沒有網路：下次再看
            self.log(f"update re-check failed: {ex!r}")
            return False
        if not info:
            return False
        version = info["version"]
        if version == (self.update_offer or {}).get("version") or version in (self._update_declined, updater.skipped(self.data)) \
                or updater.Attempts(self.data).recently_tried(version):
            return False
        notes = changes.since(updater.fetch_notes(info.get("notes_url") or ""))      # 這次更新會改什麼（拿不到就不顯示）
        self.log(f"update {version} is available (found while the app was open)")
        self._offer({"version": version, "size": info.get("size") or 0, "notes": notes}, info)
        return True

    def _update_now(self) -> None:
        """畫面上的提醒按了「現在更新」：照開啟時的流程下載、核對、安裝；沒成功（沒網路、按了取消）就回到原本的畫面照常用。"""
        before = (self.phase, dict(self.detail))
        info = self._update_info
        if info and self._install_update(info, updater.Attempts(self.data)):
            return
        self.set_phase(before[0], **before[1])

    def _update_if_needed(self) -> bool:
        """有新版本就先問使用者。選「現在更新」才下載安裝並結束程式（回傳 True）；
        選「這次先不要」或下載到一半按取消，就照常用現在的版本，下次開啟時會再問。"""
        if not updater.enabled():
            return False
        if not self.cfg["update_check"]:      # 使用者關掉了：開啟時不連網路
            self.log("update check is turned off in the settings")
            return False
        self.set_phase("updating", step="checking")
        try:
            info = updater.check()
        except Exception as ex:  # noqa: BLE001  沒有網路：照常使用
            self.log(f"update check failed: {ex!r}")
            return False
        if not info:
            return False
        attempts = updater.Attempts(self.data)
        if info["version"] == updater.skipped(self.data):      # 使用者從這一版退回來的：不再問，等更新的版本
            self.log(f"update to {info['version']} is skipped (rolled back from it)")
            return False
        if attempts.recently_tried(info["version"]):
            self.log(f"update to {info['version']} was just tried; not retrying yet")
            self.notice("update_failed", version=info["version"])
            return False
        self._update_stop.clear()
        notes = changes.since(updater.fetch_notes(info.get("notes_url") or ""))      # 這次更新會改什麼（拿不到就不顯示）
        self.set_phase("updating", step="ask", version=info["version"], notes=notes, size=info.get("size") or 0)
        choice = None
        while choice is None and not self.closing.is_set():
            try:
                choice = self._update_choices.get(timeout=0.5)
            except queue.Empty:
                continue
        if choice != "now":
            self.log(f"update to {info['version']} skipped by the user")
            self._update_declined = info["version"]
            return False
        return self._install_update(info, attempts)

    def _install_update(self, info: dict, attempts: "updater.Attempts") -> bool:
        """下載新版的安裝檔、核對、啟動安裝程式，然後結束程式讓它換檔案（回傳 True）；
        下載失敗或使用者按了取消就回傳 False，照常用現在的版本。"""
        try:
            self.set_phase("updating", step="downloading", version=info["version"], percent=0)
            last = [0.0]

            def progress(done, total):
                if time.time() - last[0] > 0.4 and total:
                    last[0] = time.time()
                    self.set_phase("updating", step="downloading", version=info["version"],
                                   percent=int(done * 100 / total))

            path = updater.download(info, self.data / "update", progress, cancel=self._update_stop.is_set)
            if self._update_stop.is_set():
                raise assets.DownloadError("cancelled")
            self.set_phase("updating", step="installing", version=info["version"])
            attempts.record(info["version"])
            updater.launch_installer(path)
        except Exception as ex:  # noqa: BLE001
            if self._update_stop.is_set():
                self.log(f"update to {info['version']} cancelled by the user during download")
                return False
            self.log(f"update failed: {ex!r}")
            self.notice("update_failed", version=info["version"])
            return False
        # 安裝程式在等這個程式關掉才能換檔案，所以不等視窗慢慢收，直接結束
        self.log(f"installing update {info['version']}; closing")
        time.sleep(0.5)
        self.exit_now()
        return True

    def update_state(self) -> dict:
        """關於頁用：previous 可以退回去的上一版（沒有就是 None）；skipped 退回之後不再問的那一版。"""
        return {"previous": updater.previous(self.data), "skipped": updater.skipped(self.data), "version": __version__}

    def update_unskip(self) -> dict:
        updater.unskip(self.data)
        return {"ok": True, **self.update_state()}

    def rollback(self) -> dict:
        """回上一版：用留著的安裝檔（沒留著就先下載），裝好自動重開。之後不會再問要不要更新到剛退掉的這一版。"""
        prev = updater.previous(self.data)
        if not prev:
            return {"ok": False, "error": "rollback_none"}
        if self._move or self.phase in ("updating", "downloading", "loading", "starting"):
            return {"ok": False, "error": "store_busy"}
        self._update_stop.clear()
        threading.Thread(target=self._rollback_work, args=(prev,), daemon=True, name="rollback").start()
        return {"ok": True, "version": prev["version"]}

    def _rollback_work(self, prev: dict) -> None:
        version, before = prev["version"], (self.phase, dict(self.detail))
        try:
            # 用過的安裝檔不留著（_installers_cleanup）：退回的時候重新下載那一版，核對過才裝
            self.set_phase("updating", step="downloading", version=version, percent=0, back=True)
            last = [0.0]

            def progress(done, total):
                if time.time() - last[0] > 0.4 and total:
                    last[0] = time.time()
                    self.set_phase("updating", step="downloading", version=version, percent=int(done * 100 / total), back=True)

            info = updater.release_of(version)
            if not info:
                raise assets.DownloadError(f"version {version} was not found")
            updater.installer_path(self.data / "update", version).unlink(missing_ok=True)      # 萬一有同名的舊檔：不用它，重新下載
            path = updater.download(info, self.data / "update", progress, cancel=self._update_stop.is_set)
            if self._update_stop.is_set():
                raise assets.DownloadError("cancelled")
            self.set_phase("updating", step="installing", version=version, back=True)
            self._settings_copy()
            updater.Attempts(self.data).record(version, skip=__version__, rollback=True)
            updater.launch_installer(path)
        except Exception as ex:  # noqa: BLE001
            self.set_phase(before[0], **before[1])      # 沒退成：回到原本的畫面，照常使用
            if self._update_stop.is_set():
                self.log(f"rollback to {version} cancelled by the user during download")
                return
            self.log(f"rollback to {version} failed: {ex!r}")
            self.notice("rollback_failed", version=version)
            return
        self.log(f"rolling back to {version}; closing")
        time.sleep(0.5)
        self.exit_now()

    def _settings_copy(self) -> None:
        """退回舊版之前把現在的設定另外存一份：舊版不認得的新設定，舊版存檔時會丟掉；之後再更新回來可以用「匯入設定」拿回來。"""
        try:
            folder = self.transcript_dir()
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"LiveLingo-settings-{__version__}-before-rollback.json").write_text(json.dumps(
                {"app": "LiveLingo", "kind": SETTINGS_KIND, "version": 1, "app_version": __version__, "config": self._settings_now()},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as ex:
            self.log(f"could not keep a copy of the settings before rolling back: {ex!r}")

    def _prepare(self) -> None:
        """決定模式、確認檔案都在、啟動翻譯引擎。"""
        self.mode = gpu.choose_mode("standard" if self.fallback else self.cfg["quality"], self.gpu)
        if self.fallback:
            self.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        need = assets.missing(self.store, self.mode)
        if need:
            self.set_phase("need_download", items=need, total_mb=sum(i["mb"] for i in need))
            return
        self._start_engine()

    def start_download(self) -> None:
        if self.phase != "need_download":
            return
        if self._move:      # 大檔案正在搬：現在下載的話會下載到舊的位置，搬完就不見了
            self.notice("store_busy")
            return
        items = self.detail.get("items") or assets.missing(self.store, self.mode)
        self._download_cancel.clear()
        self.set_phase("downloading", key=items[0]["key"], done_mb=0, total_mb=sum(i["mb"] for i in items),
                       index=0, count=len(items))

        def work():
            try:
                assets.download_all(self.store, items, lambda p: self.set_phase("downloading", **p),
                                    self._download_cancel.is_set)
                self._start_engine()
            except Exception as ex:  # noqa: BLE001
                self.log(traceback.format_exc())
                self.set_phase("need_download", items=assets.missing(self.store, self.mode),
                               total_mb=sum(i["mb"] for i in assets.missing(self.store, self.mode)),
                               error=str(ex))

        threading.Thread(target=work, daemon=True, name="download").start()

    def _start_engine(self) -> None:
        self.set_phase("loading", model=self.mode["model"], engine=self.mode["engine"])
        if self.engine:
            self.engine.stop()
        gpu_mode = self.mode["engine"] == "cuda"
        self.engine = self._new_engine()
        self.translator.url, self.translator.key = self.engine.url, self.engine.key
        try:
            self.engine.start()
            if not self.engine.wait_ready(240):
                raise RuntimeError("the translation engine did not become ready in time")
        except Exception as ex:  # noqa: BLE001
            self.log(f"engine failed ({self.mode}): {ex!r}")
            self.engine.stop()
            if gpu_mode and not self.fallback:
                # 顯示卡模式開不起來（驅動太舊、記憶體被遊戲佔滿…）：自動改用不需要顯示卡的標準模式
                self.fallback = True
                self.notice("gpu_fallback")
                self._prepare()
                return
            self.set_phase("error", message=str(ex), log=str(self.data / "engine.log"))
            return
        self.translator.clear_cache()
        self.set_phase("ready")
        self._resume_sources()
        self._warm_engine()

    def _cpu_turn(self):
        """「輪到誰用處理器」的鎖。翻譯引擎和語音辨識都用處理器的時候，兩個同時跑會互搶，
        實測（4 核心）：一句正在翻、下一句同時在辨識，翻譯 2.3 → 8.5 秒、辨識 3.7 → 6.6 秒；輪流跑的話
        翻譯照樣 2.3 秒就好，辨識排在後面也只多等那一點時間。任何一邊用顯示卡就不用輪流（回傳一個什麼都不擋的鎖）。"""
        import contextlib

        recognizer = self.recognizer
        if self.mode.get("engine") == "cpu" and getattr(recognizer, "device", "cpu") == "cpu":
            return self._cpu_lock
        return contextlib.nullcontext()

    def _warm_directions(self) -> list[tuple[str, str]]:
        """會用到的翻譯方向：對方的語言 → 我的語言，我的語言 → 對方的語言。"""
        my, their = self.cfg["my_lang"], self.cfg["their_lang"]
        return [(LANGS[their]["family"], my), (LANGS[my]["family"], their)]

    def _warm_engine(self) -> None:
        """引擎剛開好（或換了語言、語氣）：在背景先暖機，第一句不用多等那 2 秒左右（見 Translator.warm）。"""
        warm = getattr(self.translator, "warm", None)
        if not warm or self.phase != "ready":
            return

        def work():
            started = time.time()
            done = warm(self._warm_directions())
            if done:
                self.log(f"translation engine warmed up for {done} direction(s) in {time.time() - started:.1f} s")

        threading.Thread(target=work, daemon=True, name="warm").start()

    def _new_engine(self) -> Engine:
        return Engine(
            exe=assets.find_server(assets.engine_dir(self.store, self.mode["engine"])),
            model=assets.model_path(self.store, self.mode["model"]),
            log_path=self.data / "engine.log", threads=int(self.cfg["threads"]),
            gpu_layers=99 if self.mode["engine"] == "cuda" else 0)

    # ---- 翻譯引擎用到一半自己停了（記憶體被遊戲佔滿、被別的程式關掉…）：自動重開，沒翻成的句子補翻

    def _say(self, code: str) -> None:
        """程式自己發生的事：浮動留言上說一聲（人在遊戲裡），主視窗也跳一則。"""
        table = HOTKEY_NOTES.get(self.cfg["ui_lang"], HOTKEY_NOTES["en"])
        self.overlay.note(table[code])
        self.notice(code)

    def _engine_gone(self) -> bool:
        engine = self.engine
        return bool(self.phase == "ready" and not self._resting and engine and engine.proc is not None
                    and engine.proc.poll() is not None)

    def _engine_guard(self) -> None:
        while not self.closing.wait(3):
            try:
                if self._engine_gone():
                    self._revive_engine()
                self._maybe_rest()
                self._maybe_free_speech()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

    # ---- 沒在用就放掉：翻譯引擎（使用者選幾分鐘）、語音辨識模型（兩邊收音都關著）

    def _maybe_rest(self) -> None:
        minutes = int(self.cfg["engine_rest"] or 0)
        if minutes <= 0 or self._resting or self.phase != "ready" or not self.engine or self.engine.proc is None:
            return
        if time.monotonic() - self._last_use < minutes * 60:
            return
        with self._revive_lock:
            if self._resting or self.phase != "ready" or time.monotonic() - self._last_use < minutes * 60:
                return
            self._resting = True
            self._engine_ok.clear()      # 翻譯的那條線看到沒亮，會先叫醒引擎（_wake_engine）
            self.engine.stop()
            self.log(f"the translation engine is resting after {minutes} idle minutes (memory and graphics memory freed)")

    def _wake_engine(self) -> None:
        """有句子要翻：記下時間；引擎在休息的話叫醒它（等它好了才回來）。"""
        self._last_use = time.monotonic()
        if not self._resting:
            return
        with self._revive_lock:
            if not self._resting or self.closing.is_set():
                return
            # 只在浮動留言說一聲（人在遊戲裡）；主視窗不跳通知，這不是出錯
            table = HOTKEY_NOTES.get(self.cfg["ui_lang"], HOTKEY_NOTES["en"])
            self.overlay.note(table["engine_waking"])
            started = time.time()
            engine = self._new_engine()
            try:
                engine.start()
                if not engine.wait_ready(180):
                    raise RuntimeError("the translation engine did not become ready in time")
            except Exception as ex:  # noqa: BLE001
                engine.stop()
                self.engine, self._resting = engine, False
                self._engine_ok.set()
                self._engine_lost(ex, code="restart_failed")
                return
            self.engine, self._resting = engine, False
            self.translator.url, self.translator.key = engine.url, engine.key
            self._last_use = time.monotonic()
            self._engine_ok.set()
            self.log(f"the translation engine woke up in {time.time() - started:.1f} s")

    def _maybe_free_speech(self) -> None:
        if self.pipelines or (self._subs_job is not None and self._subs_job.is_alive()):
            self._voice_off_since = None
            return
        now = time.monotonic()
        if self._voice_off_since is None:
            self._voice_off_since = now
        if self.recognizer is not None and now - self._voice_off_since >= SPEECH_REST:
            with self._lock:
                if self.pipelines or self.recognizer is None:
                    return
                self.recognizer = None
            import gc

            gc.collect()
            self.log("speech recognition model released (both voice sources are off)")

    def _revive_engine(self) -> bool:
        """引擎停了就重開一個。十分鐘內重開過 REVIVE_TIMES 次還是停：不再試，照「引擎開不起來」處理。回傳有沒有重開成功。"""
        with self._revive_lock:
            if self.closing.is_set() or not self._engine_gone():
                return True      # 別的執行緒已經處理好了
            self._engine_ok.clear()
            try:
                now = time.time()
                self._revivals = [t for t in self._revivals if now - t < REVIVE_WINDOW]
                again = len(self._revivals) < REVIVE_TIMES
                self.log(f"the translation engine stopped by itself (exit code {self.engine.proc.returncode}); "
                         + (f"restart {len(self._revivals) + 1} of {REVIVE_TIMES}" if again
                            else f"not restarting, it already stopped {REVIVE_TIMES} times in {REVIVE_WINDOW / 60:.0f} minutes"))
                try:      # 重開會把 engine.log 蓋掉：先留一份，才看得到它為什麼停
                    (self.data / "engine-stopped.log").write_bytes((self.data / "engine.log").read_bytes()[-200_000:])
                except OSError:
                    pass
                if len(self._revivals) >= REVIVE_TIMES:
                    return self._engine_lost(RuntimeError("the translation engine keeps stopping (see engine-stopped.log)"),
                                             code="keeps_stopping")
                self._revivals.append(now)
                self._say("engine_restarting")
                self.engine.stop()
                engine = self._new_engine()
                try:
                    engine.start()
                    if not engine.wait_ready(180):
                        raise RuntimeError("the translation engine did not become ready in time")
                except Exception as ex:  # noqa: BLE001
                    engine.stop()
                    return self._engine_lost(ex, code="restart_failed")
                if self.closing.is_set():
                    engine.stop()
                    return False
                self.engine = engine
                self.translator.url, self.translator.key = engine.url, engine.key
                self.log("the translation engine is running again")
                self._say("engine_back")
                return True
            finally:
                self._engine_ok.set()

    def _engine_lost(self, ex: Exception, code: str = "") -> bool:
        """code：介面用使用者的語言說明的原因代碼（err_代碼）；沒有的話就顯示電腦回報的原文。"""
        self.log(f"the translation engine could not be restarted ({self.mode}): {ex!r}")
        if self.mode["engine"] == "cuda" and not self.fallback:
            # 顯示卡模式一直停（最常見：顯示記憶體被遊戲佔滿）：改用不需要顯示卡的標準模式
            self.fallback = True
            self.notice("gpu_fallback")
            self._prepare()
            return self.phase == "ready"
        self.set_phase("error", message=str(ex), code=code,
                       log=str(self.data / ("engine-stopped.log" if code == "keeps_stopping" else "engine.log")))
        return False

    def _resume_sources(self) -> None:
        """上次關掉程式時開著的來源，這次自動再打開。"""
        if not self.cfg["setup_done"]:
            return
        want = self.cfg["sources"]
        if want.get("screen") and self._regions_on():
            self.set_source("screen", True)
        for key in ("listen", "mic"):
            if want.get(key):
                self.set_source(key, True)

    # ------------------------------------------------------------------ 翻譯

    def _hints(self, text: str, source: str, target: str) -> list[tuple[str, str]]:
        if not self.cfg["game_terms"]:
            return []
        return self.terms.find(text, source, target)

    def _terms_changed(self) -> None:
        """換了用語包（或換了遊戲）：重新載入，提示前面那句話也跟著換（沒有選英雄聯盟就不說是在聊英雄聯盟）。"""
        from .core.translate import HINT_INTRO, HINT_INTRO_CHAT

        self.terms = TermPack(packs_for(self.cfg["term_packs"], self.game, bool(self.cfg["game_auto"])))
        self.translator.hint_intro = HINT_INTRO if "lol" in self.terms.names else HINT_INTRO_CHAT
        self.translator.clear_cache()

    def stream_action(self, action: str) -> dict:
        """直播模式的按鈕：copy = 把給 OBS 的網址複製起來。"""
        if action == "copy":
            url = self.stream.url()
            return {"ok": bool(url) and oswin.copy_text(url), "url": url}
        return {"ok": False}

    def _game_words(self) -> str:
        """遊戲專用詞要用哪個遊戲的：開著「照遊戲自動切換」而且認出了遊戲才有。"""
        return self.game if self.cfg["game_auto"] and self.cfg["game_terms"] else ""

    def game_state(self) -> dict:
        return {"exe": self.game, "name": game_name(self.game) if self.game else "", "auto": bool(self.cfg["game_auto"]),
                "packs": list(self.terms.names),
                "own": [{"exe": g, "name": game_name(g)} for g in sorted(self.glossary.games())]}

    def _game_watch(self) -> None:
        """每幾秒看一次現在在玩什麼遊戲；換了就換用語包和遊戲專用詞，並告訴使用者。只看視窗標題和程式檔名。"""
        while not self.closing.wait(GAME_EVERY):
            try:
                self.check_game()
                self.check_auto_start()
                windows = apps.windows() if (self.cfg["game_profiles"] or self.cfg["fullscreen_warn"]) else []
                self.check_profile(windows)
                self.check_fullscreen(windows)
                if time.time() - self._recap_checked >= RECAP_EVERY:
                    self._recap_checked = time.time()
                    self.recap_check()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

    def check_game(self) -> str:
        found = ""
        if self.cfg["game_terms"] and self.cfg["game_auto"]:
            found = find_game(apps.windows(), self.glossary.games())
        if found != self.game:
            self.game = found
            self._terms_changed()
            self.log(f"game now: {found or '-'} packs={self.terms.names}")
            self.hub.publish("game", self.game_state())
            if found:
                self.notice("game_now", game=game_name(found))
        return found

    def _pronunciation(self, text: str, target: str) -> str:
        # 不管「顯示唸法」有沒有開都先算好，介面照設定決定要不要顯示，這樣開關一切換舊的句子也會跟著變
        return romanize(text) if LANGS[target]["family"] == "ko" else ""

    def _post(self, msg: dict) -> dict:
        if msg.get("speaker"):      # 翻譯的時候使用者可能剛幫這個人改了名字，或按了重新分辨：名字以現在的為準，不用送去翻譯時的那一份
            current = msg.get("speaker_run") == self._speaker_run
            msg = dict(msg, voice=self._speaker_names.get(int(msg["speaker"]), "") if current else "")
        with self._lock:
            for i, old in enumerate(self.messages):
                if old["id"] == msg["id"]:
                    self.messages[i] = msg
                    break
            else:
                self.messages.append(msg)
        self.hub.publish("message", msg)
        if self._hidden(msg):      # 沒把握的句子設成不顯示（2.15.0）：浮動字幕、直播字幕都不出（主視窗由介面決定）
            self.overlay.on_removed(msg["id"])
            self.stream.on_removed(msg["id"])
        else:
            self.overlay.on_message(msg)
            self.stream.on_message(msg)
        if not self.private and self.history.add(msg):      # 隱私暫停的時候不記
            self.hub.publish("history", self.history.state())
        if self.transcript.on and not self.private:
            who = self.speaker_tag(msg)      # 字幕檔只寫現在還算數的說話者（重新分辨之前標的不寫）
            self.transcript.add(dict(msg, speaker=who[0], voice=who[1]) if who else {k: v for k, v in msg.items() if k != "speaker"})
            self.hub.publish("record", self.record_state())
        return msg

    def _drop(self, msg_id: int) -> None:
        with self._lock:
            for old in list(self.messages):
                if old["id"] == msg_id:
                    self.messages.remove(old)
            unpin = msg_id in self.pins
        self.hub.publish("message_removed", {"id": msg_id})
        if unpin:
            self._unpin_gone(msg_id)
        self.overlay.on_removed(msg_id)
        self.stream.on_removed(msg_id)
        self.transcript.drop(msg_id)
        self.history.drop(msg_id)

    # ------------------------------------------------------------------ 字幕記錄

    def transcript_dir(self) -> Path:
        chosen = str(self.cfg["transcript_dir"] or "").strip()
        if chosen:
            return Path(chosen)
        return (oswin.documents_dir() / "LiveLingo") if oswin.IS_WIN else (self.data / "transcripts")

    def record_state(self) -> dict:
        return self.transcript.state(self.transcript_dir())

    def set_recording(self, on: bool, wait: float = 20.0) -> dict:
        """
        開始或結束字幕記錄。結束時把這段時間的字幕存成 txt 檔；一句都沒有就不存。
        按結束的時候還有句子在翻：最多等 wait 秒讓它們翻完再存（在背景等，畫面先顯示「儲存中」）。
        """
        if on and not self.transcript.on:
            self.transcript.start()
        elif not on and self.transcript.on and not self.transcript.closing:
            self.transcript.closing = True
            if wait > 0 and self.transcript.has_pending():
                threading.Thread(target=self._finish_recording, args=(wait,), daemon=True).start()
            else:
                self._finish_recording(0)
                return self.record_state()
        state = self.record_state()
        self.hub.publish("record", state)
        return state

    def _finish_recording(self, wait: float) -> None:
        self.transcript.close(wait)
        try:
            path = self.transcript.stop(self.transcript_dir(), self.cfg["ui_lang"], bool(self.cfg["transcript_original"]))
            if path is None:
                self.notice("record_empty")
            else:
                self.log(f"subtitle log saved: {path}")
        except OSError as ex:
            self.log(f"subtitle log could not be saved: {ex!r}")
            self.transcript.closing = False      # 沒存成功：記錄留著，換個資料夾可以再按一次結束
            self.notice("record_failed", error=str(ex))
        self.hub.publish("record", self.record_state())

    def open_transcript(self, what: str) -> bool:
        target = self.transcript.last if what == "file" else self.transcript_dir()
        if not target:
            return False
        if what != "file":
            try:
                Path(target).mkdir(parents=True, exist_ok=True)
            except OSError:
                return False
        return oswin.open_path(target)

    def choose_transcript_dir(self) -> bool:
        """讓使用者用 Windows 的視窗選字幕記錄要存在哪。"""
        if not self.window or not hasattr(self.window, "choose_folder"):
            return False
        picked = self.window.choose_folder(str(self.transcript_dir()))
        if not picked:
            return False
        ok = self.cfg.set(transcript_dir=picked)
        self.hub.publish("config", self.cfg.snapshot())
        self.stored("transcript_dir", ok)
        self.hub.publish("record", self.record_state())
        return True

    def delay(self) -> dict:
        """最近幾句語音平均花了多久：{"hear": 辨識秒數, "translate": 翻譯秒數, "count": 幾句}。"""
        rows = list(self.delays)
        if not rows:
            return {"hear": 0.0, "translate": 0.0, "count": 0}
        return {"hear": round(sum(r[0] for r in rows) / len(rows), 1),
                "translate": round(sum(r[1] for r in rows) / len(rows), 1), "count": len(rows)}

    def submit(self, via: str, text: str, lang: str | None = None, unsure: bool = False, hear: float = 0.0,
               place: str = "", live: dict | None = None, speaker: int | None = None, shown: str = "", asked: bool = False,
               tint: str = "", story: bool = False, loud: float = 0.0) -> int | None:
        """via：screen（螢幕文字）/ heard（對方的聲音）/ spoken（我說的話）/ typed（我打的字）。
        unsure：辨識的時候沒什麼把握（介面會標「可能聽錯／看錯」，可以改原文重翻）。
        hear：語音辨識花了幾秒。place：螢幕上哪一個區域讀到的（開著不只一個區域時才有）。
        live：這一句還在說的時候已經先顯示出來的那一則（即時字幕）；整句接手同一則，不另外多一則。
        speaker：第幾個說話者（有開「分辨說話者」而且分得出來才有）。
        shown：畫面上現成的翻譯（雙語字幕裡我的語言那一行）；有的話直接用，不叫模型翻。
        asked：使用者自己按快速鍵要翻的（翻選取的字、框選翻譯）：排在最前面，也不會因為等太久被略過。回傳這則訊息的編號。"""
        text = text.strip()[:MAX_TEXT]
        if not text:
            if live:
                self._drop(live["id"])
            return None
        lang = lang or detect_language(text) or "en"
        who = "them" if via in ("screen", "heard") else "me"
        work = self._work_now(via, place)
        msg = {"id": next(self._ids), "who": who, "via": via, "original": text, "lang": lang,
               "translated": "", "pron": "", "back": "", "copied": False, "pending": True, "error": "",
               "unsure": bool(unsure) and via != "typed", "time": time.strftime("%H:%M"), "at": time.time(),
               "hear": round(float(hear), 1), "took": 0.0, "place": str(place)[:30], "work": work}
        if shown and via == "screen":
            msg["shown"] = str(shown).strip()[:MAX_TEXT * 2]
        if tint and via == "screen":
            msg["tint"] = tint      # 聊天頻道的顏色（2.16.0）
        if story and via == "screen":
            msg["story"] = True     # 劇情對話框讀到的：翻好記進劇情書
        if speaker:
            msg.update(speaker=int(speaker), speaker_run=self._speaker_run, voice=self._speaker_names.get(int(speaker), ""))
        if who == "them" and self.cfg["tone_icons"]:
            feel = tone_mod.guess(text, loud)      # 語氣小圖示（2.18.0）：在笑、生氣、開玩笑、難過
            if feel:
                msg["tone"] = feel
        if live:      # 同一則、同一個時間，先翻出來的半句留在畫面上，等整句翻好再換掉
            msg.update(id=live["id"], time=live["time"], at=live["at"], partial=live.get("partial", ""))
        if via != "screen":      # 螢幕上讀到的要翻完才知道值不值得顯示，其他的先把原文顯示出來
            self._post(msg)
        self.jobs.put(msg, asked=asked)
        return msg["id"]

    def _work_now(self, via: str, place: str = "") -> str:
        """這一句是在哪一部影片、哪一個網頁看到的（works.work_key）；自己打的、說的，或關掉「記住在看哪一部」就是空的。
        讀畫面：區域跟著的程式的視窗，沒跟就是區域中間那個視窗；聽聲音：只聽的那個程式，否則最前面的視窗。
        第一次看到之前翻過的那一部，跳一則提示「這部之前翻過 N 句」。"""
        if via not in ("screen", "heard") or not self.cfg["remember_works"]:
            return ""
        now = time.monotonic()
        at, last_via, last_place, last = self._work_seen
        if now - at < 2.0 and (via, place) == (last_via, last_place):
            return last
        exe, point = "", None
        if via == "screen":
            on = [r for r in self.cfg["regions"] if r.get("on")]
            region = next((r for r in on if place and r.get("name") == place), on[0] if on else None)
            if region:
                exe = str(region.get("app") or "")
                point = (int(region.get("left", 0)) + int(region.get("width", 0)) // 2,
                         int(region.get("top", 0)) + int(region.get("height", 0)) // 2)
        elif self.cfg["listen_apps"]:
            exe = str(self.cfg["listen_apps"][0])
        try:
            win = works.pick_window(apps.windows(), exe=exe, point=None if exe else point)
        except Exception:  # noqa: BLE001  拿不到視窗清單就當作不知道在看哪一部
            win = None
        title = works.clean_title(win["title"]) if win else ""
        key = works.work_key(title)
        self._work_seen = (now, via, place, key)
        if key and key != last:
            info = self.memory.work_info(key)
            if not self.private:
                self.memory.saw(key, title)
            if info and key not in self._work_told and (info["mine"] or (info["shared"] and self.cfg["use_shared"] and not info["own_only"])):
                self._work_told.add(key)
                self.notice("work_known", title=title, n=info["mine"] + info["shared"], mine=info["mine"], shared=info["shared"])
        return key

    def _target_for(self, msg: dict) -> str:
        my, their = self.cfg["my_lang"], self.cfg["their_lang"]
        if msg["who"] == "them":
            return my
        if LANGS[my]["family"] == LANGS[their]["family"]:
            return their      # 兩邊是同一種語言（例如繁體和簡體）：我說的一律給對方
        # 我打的字、我說的話：是我的語言就翻成對方的語言，否則翻回我的語言
        return their if msg["lang"] == LANGS[my]["family"] else my

    def _context_for(self, msg: dict) -> list[str]:
        """這一句前面最近的兩句對話（原文），給翻譯模型參考。只看兩分鐘內、語音和打字的句子；
        螢幕上讀到的是一行一行各自獨立的文字，不拿來當前文，也不用前文。"""
        if not self.cfg["use_context"] or msg.get("via") == "screen":
            return []
        at = msg.get("at") or time.time()
        with self._lock:
            before = [m for m in self.messages
                      if m["id"] < msg["id"] and m.get("via") != "screen" and not m.get("error")
                      and at - (m.get("at") or 0) <= 120]
        return [m["original"] for m in before[-2:]]

    def _translate_worker(self) -> None:
        while not self.closing.is_set():
            try:
                self._translate_next()
            except Exception:  # noqa: BLE001  這條線停了就再也沒有翻譯，所以不管出什麼錯都要繼續
                self.log(traceback.format_exc())
                time.sleep(0.5)

    def _translate_next(self) -> None:
        """從排隊裡拿一句來翻（沒有就等半秒）。"""
        try:
            msg, late = self.jobs.get(timeout=0.5)
        except queue.Empty:
            self._tell_skipped(force=True)
            return
        if late:      # 螢幕上讀到的、排了太久的：不翻了（還沒顯示過，所以畫面上不會留下半則）
            self._skipped += 1
            self._tell_skipped()
            self._card_done(msg, None)
            return
        self._wake_engine()      # 引擎在休息的話先叫醒
        if not self._engine_ok.is_set():
            self._engine_ok.wait(300)      # 翻譯引擎正在自動重開：等它回來再翻，不要每一句都報失敗
        if "back_of" in msg:
            self._back_translate(msg["back_of"])
            return
        if msg.get("live"):
            self._translate_live(msg)
            return
        try:
            target = self._target_for(msg)
            started = time.time()
            out = self._translate(msg, target)
            spent = time.time() - started
            msg = {k: v for k, v in msg.items() if k != "partial"}      # 先翻的半句用完了，不跟著整句存下去
            if msg["via"] in ("heard", "spoken") and not msg.get("edited") and not msg.get("took"):
                self.delays.append((float(msg.get("hear") or 0.0), spent))      # 只算第一次翻的，重翻的不算
                self.hub.publish("delay", self.delay())
                msg = dict(msg, took=round(float(msg.get("hear") or 0.0) + spent, 1))
            if msg["via"] == "screen" and (not out or out.strip() == msg["original"].strip()):
                self._card_done(msg, out or msg["original"])      # 本來就是我的語言：小卡上照原樣顯示
                return
            if msg.get("unsure") and self.cfg["unsure_recheck"] and msg["via"] in ("heard", "screen") and out.strip():
                msg = dict(msg, unsure=self._still_unsure(msg, target, out))
            msg = dict(msg, translated=out, target=target, pending=False, error="", why="", back="",
                       notes=self._slang_notes(msg, target),      # 網路用語、縮寫的意思：譯文下面的小字（2.15.0）
                       players=gametools.players_in(msg["original"], self.cfg["player_notes"]) if msg["who"] == "them" else [],
                       mention=self._mention_of(msg, out),      # 對方的話裡有我的名字或關鍵字
                       lost=numbers_lost(msg["original"], out, target, msg["lang"]),      # 原文有、譯文找不到的數字：畫面會提醒核對
                       pron=self._pronunciation(out, target) if msg["who"] == "me" else "")
            self._count(msg)
            if msg["who"] == "me" and target != self.cfg["my_lang"] and not msg.get("copied"):
                if msg["via"] == "typed" or self.cfg["copy_spoken"]:
                    msg["copied"] = oswin.copy_text(out)
            self._post(msg)
            self._alert(msg)
            self._card_done(msg, out)
            self._speak_mine(msg, target, out)
            self._chat_send(msg, out)
            self._read_heard(msg, target, out)
            self._watch_names(msg)
            self._watch_abbr(msg)
            if msg.get("story") and self.cfg["story_book"] and not self.private:
                self.storybook.add(self.game or self._screen_app(), msg["original"], out)
            self._learn(msg)
            self._want_back(msg)
        except Exception as ex:  # noqa: BLE001
            if not self._engine_gone():
                time.sleep(0.5)      # 連線先斷、程式晚一點點才真的結束
            if self._engine_gone() and self._retries.get(msg["id"], 0) < 2:
                # 引擎剛好在翻這一句的時候停了：不算失敗，重開之後補翻（排最前面，也不會因為等太久被略過）
                self._retries[msg["id"]] = self._retries.get(msg["id"], 0) + 1
                self.jobs.put(msg, asked=True)
                self._revive_engine()
                return
            self.log(traceback.format_exc())
            self._count(msg, failed=True)
            self._post(dict(msg, pending=False, error=str(ex), why=why(ex)))
            self._card_done(msg, None)

    def _translate(self, msg: dict, target: str) -> str:
        """翻一則。翻的時候譯文一邊出來一邊送給畫面（即時），最後回傳的才是整理過的整句。"""
        import inspect

        if msg.get("shown") and self.cfg["use_shown_translation"] and target == self.cfg["my_lang"] and not msg.get("edited"):
            # 雙語字幕：畫面上已經有我的語言那一行，直接用（繁簡照我的設定轉），也記下來，之後同一句不用重翻
            from .core.translate import to_script

            out = to_script(msg["shown"], target)
            if msg.get("lang"):
                self.memory.seen_on_screen(msg["original"], msg["lang"], target, out, msg.get("work") or "")
            msg["kept"] = "screen"
            return out
        fn = self.translator.translate
        self.translator.model = self.mode.get("model", "")
        if "on_part" not in inspect.signature(fn).parameters:
            return fn(msg["original"], target, msg["lang"], self._context_for(msg))
        more = {"remember": msg.get("via") or ""} if "remember" in inspect.signature(fn).parameters and not self.private else {}
        if more and msg.get("work") and "work" in inspect.signature(fn).parameters:
            more["work"] = msg["work"]
        sent, shown = [0.0], msg.get("partial") or ""

        def part(text: str) -> None:
            now = time.monotonic()
            if now - sent[0] < PARTIAL_EVERY or not text.strip() or shown.startswith(text):
                return      # 畫面上已經有先翻的半句，新的還跟它開頭一樣：不用換，字才不會縮回去又長出來
            sent[0] = now
            self.hub.publish("partial", {"id": msg["id"], "text": text})
            self.overlay.on_partial(msg, text)
            self.stream.on_partial(msg, text)

        out = fn(msg["original"], target, msg["lang"], self._context_for(msg), on_part=part, **more)
        came = self.translator.came_from() if more and hasattr(self.translator, "came_from") else None
        msg["kept"] = came["from"] if came else ""      # 用的是記住的翻法：介面在旁邊標「翻過的」「朋友的」「字幕」
        if more and time.monotonic() - self._memory_told > 10:      # 設定畫面上「記住幾句」的數字，最多 10 秒更新一次
            self._memory_told = time.monotonic()
            self.hub.publish("memory", self.memory.state())
        return out

    # ---- 即時字幕：對方還在說，就先把目前聽到的顯示出來、先翻

    def _on_live(self, kind: str, text: str | None, lang: str | None) -> None:
        """語音那邊在一句話還沒說完時叫的：text 是目前聽到的內容；None 表示那一句最後沒有結果，把先顯示的收回。"""
        via = "heard" if kind == "loopback" else "spoken"
        with self._lock:
            old = self._live.get(kind)
        if text is None:
            self._end_live(kind)
            return
        if text == "":      # 說完了，整句正在辨識：半句不用再翻（畫面上先留著，等整句來接）
            if old:
                self._set_live(kind, dict(old, closing=True))
                self.jobs.drop_live(via)
                self._stop_live(kind)
            return
        text = self._voice_script(kind, text.strip()[:MAX_TEXT], lang)
        if not text or (old and old["original"] == text):
            return
        if old:
            msg = dict(old, original=text, lang=lang, closing=False)
        else:
            msg = {"id": next(self._ids), "who": "them" if via == "heard" else "me", "via": via, "original": text, "lang": lang,
                   "translated": "", "pron": "", "back": "", "copied": False, "pending": True, "error": "", "unsure": False,
                   "time": time.strftime("%H:%M"), "at": time.time(), "hear": 0.0, "took": 0.0, "place": "",
                   "live": True, "partial": ""}
        self._set_live(kind, msg)
        self._stop_live(kind)      # 上一份半句如果還在翻：停，換翻新的
        self.hub.publish("message", msg)
        self.jobs.put_live(msg)

    def _stop_live(self, kind: str) -> None:
        stop = self._live_stop.pop(kind, None)
        if stop:
            stop.stop()

    def _live_wait(self) -> bool:
        """先不要做「還在說就先辨識」：翻譯引擎和語音辨識都用處理器的時候，兩個一起跑會互搶，兩邊都慢好幾倍。
        所以那種電腦上，正在翻或有句子在等翻的時候就先不辨識半句。翻譯用顯示卡的電腦不用等。"""
        if self.mode.get("engine") != "cpu":
            return False
        busy = getattr(self.translator, "busy", None)
        return bool(busy and busy()) or self.jobs.waiting() > 0

    def _set_live(self, kind: str, msg: dict) -> None:
        """記下還在說的那一則（只放進目前的對話，不進歷史、字幕檔、使用數字：那些等整句）。"""
        with self._lock:
            self._live[kind] = msg
            for i, old in enumerate(self.messages):
                if old["id"] == msg["id"]:
                    self.messages[i] = msg
                    break
            else:
                self.messages.append(msg)

    def _take_live(self, kind: str) -> dict | None:
        """整句來了：把先顯示的那一則交出來（之後由整句接手）。"""
        with self._lock:
            live = self._live.pop(kind, None)
        if live:
            self.jobs.drop_live(live["via"])
            self._stop_live(kind)
        return live

    def _end_live(self, kind: str) -> None:
        live = self._take_live(kind)
        if live:
            self._drop(live["id"])

    def _translate_live(self, job: dict) -> None:
        """翻還在說的半句。聽到更多、或整句來了，這一次就不用翻完（馬上停，讓位給新的）。"""
        kind = "loopback" if job["via"] == "heard" else "mic"

        def current() -> dict | None:
            with self._lock:
                live = self._live.get(kind)
            return live if live and live["id"] == job["id"] and live["original"] == job["original"] and not live.get("closing") else None

        def show(text: str) -> None:
            live = current()
            if live and text.strip():
                self._set_live(kind, dict(live, partial=text))
                self.hub.publish("partial", {"id": job["id"], "text": text})
                self.overlay.on_partial(job, text)
                self.stream.on_partial(job, text)

        import inspect

        fn = self.translator.translate
        if not current() or not self._engine_ok.is_set():
            return
        sent, shown = [0.0], job.get("partial") or ""

        def part(text: str) -> bool:
            if not current():
                return False
            now = time.monotonic()
            if now - sent[0] >= PARTIAL_EVERY and not shown.startswith(text):      # 跟畫面上的開頭一樣就先不換（見 _translate）
                sent[0] = now
                show(text)
            return True

        stop = self._live_stop[kind] = Stop()
        try:
            if not current():      # 剛好在這一瞬間說完了
                return
            if "on_part" in inspect.signature(fn).parameters:
                out = fn(job["original"], self._target_for(job), job["lang"], self._context_for(job), on_part=part,
                         unfinished=True, stop=stop)
            else:
                out = fn(job["original"], self._target_for(job), job["lang"], self._context_for(job))
        except Stopped:
            return
        except Exception as ex:  # noqa: BLE001  半句翻不出來不算失敗：整句來的時候會照平常的方式處理
            self.log(f"live caption not translated: {ex!r}")
            return
        finally:
            if self._live_stop.get(kind) is stop:
                self._live_stop.pop(kind, None)
        show(out)

    def _tell_skipped(self, force: bool = False) -> None:
        """聊天刷得比翻譯快、有句子被略過時跟使用者說一聲（幾秒說一次，不要一則說一次）。"""
        if not self._skipped:
            return
        self._skip_told = self._skip_told or time.time()      # 這一批第一則被略過的時間
        if not force and time.time() - self._skip_told < 5:
            return      # 先累積：隊伍排空了，或過了五秒，再一次講
        n, self._skipped, self._skip_told = self._skipped, 0, 0.0
        self.log(f"{n} screen lines were skipped: they had waited more than {self.jobs.stale:.0f}s for translation")
        table = HOTKEY_NOTES.get(self.cfg["ui_lang"], HOTKEY_NOTES["en"])
        self.overlay.note(table["skipped"].format(n=n))      # 人在遊戲裡：顯示在浮動留言上
        self.notice("skipped", n=n)

    def _count(self, msg: dict, failed: bool = False) -> None:
        """算進使用數字：一句只算一次。等多久 = 辨識的秒數 + 從這句出現到翻好的秒數。"""
        if msg["id"] in self._counted or self.private:
            return
        self._counted.add(msg["id"])
        self.usage.add(msg["via"], wait=float(msg.get("hear") or 0.0) + max(0.0, time.time() - float(msg.get("at") or time.time())), failed=failed)

    def usage_state(self) -> dict:
        return {"ok": True, **self.usage.summary()}

    def usage_clear(self) -> dict:
        removed = self.usage.clear()
        return {"ok": not self.usage.error, "removed": removed, **self.usage.summary()}

    def _want_back(self, msg: dict) -> None:
        """我說的話翻成對方的語言之後，排一個回譯（排在佇列最後面，不會擋到後面進來的句子）。"""
        if (self.cfg["back_translate"] and msg["who"] == "me" and msg.get("translated") and msg.get("target")
                and LANGS[msg["target"]]["family"] != LANGS[self.cfg["my_lang"]]["family"]):
            self.jobs.put({"back_of": msg["id"], "text": msg["translated"]})

    def _back_translate(self, msg_id: int) -> None:
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if not msg or msg.get("pending") or not msg.get("translated") or not msg.get("target"):
            return
        text = msg["translated"]
        try:
            back = self.translator.translate(text, self.cfg["my_lang"], LANGS[msg["target"]]["family"])
        except Exception:  # noqa: BLE001  回譯只是輔助，翻不出來就不顯示
            self.log(traceback.format_exc())
            return
        with self._lock:      # 等回譯的這段時間句子可能被重翻或刪掉了：只在還是同一句的時候才寫回去
            for i, old in enumerate(self.messages):
                if old["id"] == msg_id and old.get("translated") == text and not old.get("pending"):
                    self.messages[i] = msg = dict(old, back=back)
                    break
            else:
                return
        self.hub.publish("message", msg)      # 只更新對話畫面；浮動留言和字幕記錄不用重來

    def fix(self, msg_id: int, src: str, dst: str) -> bool:
        """「翻錯了？」：把這個詞記進詞彙表，然後重新翻這一則。"""
        if not self.glossary.add(src, dst):
            return False
        self.hub.publish("glossary", self.glossary.list())
        self.translator.clear_cache()
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if msg:
            msg = dict(msg, pending=True, copied=False)
            self._post(msg)
            self.jobs.put(msg, asked=True)
        return True

    def edit(self, msg_id: int, text: str) -> bool:
        """「聽錯了／看錯了」：把辨識出來的原文改成對的，重新翻這一則。"""
        text = text.strip()[:MAX_TEXT]
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if not msg or not text or msg.get("pending") or text == msg["original"]:
            return False
        if not self.private and msg.get("via") in ("heard", "screen", "spoken"):
            self._correct("seen" if msg["via"] == "screen" else "heard", msg["original"], text, msg.get("lang") or "")
        msg = dict(msg, original=text, lang=detect_language(text) or msg["lang"], unsure=False, edited=True,
                   pending=True, copied=False, taught=False, back="", error="")
        self._post(msg)
        self.jobs.put(msg, asked=True)
        return True

    def again(self, msg_id: int) -> bool:
        """翻譯失敗的那一則再翻一次（排最前面）。"""
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if not msg or msg.get("pending") or not msg.get("error"):
            return False
        msg = dict(msg, pending=True, error="", why="")
        self._post(msg)
        self.jobs.put(msg, asked=True)
        return True

    def teach(self, msg_id: int, dst: str) -> bool:
        """「翻錯了？」的另一種教法：直接告訴它整句應該怎麼翻。
        之後一模一樣的句子直接用這個翻法，類似的句子會拿它當範例。"""
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        dst = dst.strip()
        if not msg or not dst or msg.get("pending") or not msg.get("target"):
            return False
        if not self.lessons.add(msg["original"], dst, msg["lang"], msg["target"], before=msg.get("translated") or ""):
            return False
        if not self.private:
            self._correct("sentence", msg["original"], dst, msg.get("lang") or "", msg["target"])
        if msg.get("lang"):
            self.memory.fix(msg["original"], msg["lang"], msg["target"], getattr(self.translator, "memory_tone", lambda: self.cfg["tone"])(), dst, self.mode.get("model", ""),
                            msg.get("work") or "")
            self.hub.publish("memory", self.memory.state())
        self.translator.clear_cache()
        self.hub.publish("lessons", self.lessons.list())
        msg = dict(msg, translated=dst, error="", taught=True, back="", kept="",
                   pron=self._pronunciation(dst, msg["target"]) if msg["who"] == "me" else "")
        if msg["who"] == "me" and msg["target"] != self.cfg["my_lang"]:
            msg["copied"] = oswin.copy_text(dst)
        self._post(msg)
        self._want_back(msg)
        return True

    def lesson_undo(self, src: str, target: str) -> dict:
        """教過的句子退回上一版（2.15.0）。退回後的翻法照樣是「這一句就這樣翻」。"""
        dst = self.lessons.undo(src, target)
        if not dst:
            return {"ok": False}
        item = next((i for i in self.lessons.list() if i["src"] == src and i["target"] == target), None)
        if item and item.get("lang"):
            self.memory.fix(src, item["lang"], target, getattr(self.translator, "memory_tone", lambda: self.cfg["tone"])(), dst,
                            self.mode.get("model", ""), "")
            self.hub.publish("memory", self.memory.state())
        self.translator.clear_cache()
        self.hub.publish("lessons", self.lessons.list())
        return {"ok": True, "dst": dst}

    def _correct(self, kind: str, wrong: str, right: str, lang: str = "", target: str = "") -> None:
        if self.corrections.add(kind, wrong, right, lang, target):
            self.hub.publish("corrections", self.corrections.report())

    def corrections_report(self) -> dict:
        return self.corrections.report()

    def corrections_clear(self) -> dict:
        self.corrections.clear()
        self.hub.publish("corrections", self.corrections.report())
        return {"ok": True}

    def lesson_remove(self, src: str, target: str) -> None:
        self.lessons.remove(src, target)
        self.memory.unfix(src, target)
        self.translator.clear_cache()
        self.hub.publish("lessons", self.lessons.list())
        self.hub.publish("memory", self.memory.state())

    def glossary_add(self, src: str, dst: str, game: str = "") -> bool:
        """game：只在這個遊戲用（程式檔名）；空的 = 每個遊戲都用。"""
        had = self.glossary.games()
        ok = self.glossary.add(src, dst, game)
        if ok and game and game.lower() not in had:      # 第一次替這個程式加專用詞：馬上看它是不是正開著
            threading.Thread(target=self.check_game, daemon=True).start()
        if ok:
            self.translator.clear_cache()
            self.hub.publish("glossary", self.glossary.list())
            self.hub.publish("game", self.game_state())
            if not self.private:
                self._correct("word", src, dst)
        return ok

    def glossary_remove(self, src: str, game: str = "") -> None:
        self.glossary.remove(src, game)
        self.translator.clear_cache()
        self.hub.publish("glossary", self.glossary.list())
        self.hub.publish("game", self.game_state())

    def export_library(self) -> dict:
        """把我的詞彙表和教過的句子存成一個檔案（放在字幕記錄的資料夾），可以備份或傳給朋友。"""
        words, sentences = self.glossary.list(), self.lessons.list()
        folder = self.transcript_dir()
        path = folder / f"LiveLingo-library-{time.strftime('%Y%m%d-%H%M%S')}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"app": "LiveLingo", "kind": LIBRARY_KIND, "version": 1,
                                        "glossary": words, "lessons": sentences}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as ex:
            self.log(f"library export failed: {ex!r}")
            return {"ok": False, "error": str(ex)}
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "words": len(words), "sentences": len(sentences)}

    def import_library(self, data) -> dict:
        """匯入別人（或自己以前）匯出的檔案：只加原本沒有的，原本就有的不動。"""
        if isinstance(data, dict) and data.get("kind") == MEMORY_KIND:
            return {"ok": False, "error": "is_pack"}      # 選到翻譯分享檔了：那個要用「翻過的句子」的匯入
        if not isinstance(data, dict) or data.get("kind") != LIBRARY_KIND:
            return {"ok": False, "error": "not_library"}
        from .core.translate import SOURCES

        given = sum(len(data.get(k)) for k in ("glossary", "lessons") if isinstance(data.get(k), list))
        words = self.glossary.merge(data.get("glossary"))
        sentences = self.lessons.merge(data.get("lessons"), sources=tuple(SOURCES), targets=tuple(LANGS))
        if words or sentences:
            self.translator.clear_cache()
            self.hub.publish("glossary", self.glossary.list())
            self.hub.publish("lessons", self.lessons.list())
        return {"ok": True, "words": words, "sentences": sentences, "skipped": max(0, given - words - sentences)}

    # ---- 翻過的句子：分享檔、清除

    def memory_export(self, range_key: str = "all", with_mine: bool = False, work: str = "", title: str = "") -> dict:
        """把我翻過的句子存成分享檔（放在字幕記錄的資料夾），朋友匯入之後同樣的句子就不用重翻。
        預設不含我自己打的、說的（可能是私人聊天）。work：只匯出這一部（帶著標題，朋友看同一部就認得）；title：改用這個名字。"""
        if range_key not in MEMORY_RANGES:
            range_key = "all"
        data = self.memory.export(range_key, with_mine, work=work, title=title)
        if not data["items"]:
            return {"ok": False, "error": "empty"}
        folder = self.transcript_dir()
        named = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", data["works"][0]["title"]).strip()[:40] if data["works"] else ""
        path = folder / f"LiveLingo-translations-{named + '-' if named else ''}{time.strftime('%Y%m%d-%H%M%S')}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as ex:
            self.log(f"translations export failed: {ex!r}")
            return {"ok": False, "error": str(ex)}
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "sentences": len(data["items"])}

    def memory_import(self, data, name: str = "") -> dict:
        """匯入朋友的分享檔，當成一包；之後可以整包刪掉。"""
        from .core.translate import SOURCES

        if isinstance(data, dict) and data.get("kind") == LIBRARY_KIND:
            return {"ok": False, "error": "is_library"}      # 選到詞彙檔了：那個要用上面的「匯入」
        name = re.sub(r"\.json$", "", str(name or ""), flags=re.I)
        result = self.memory.import_pack(data, name, sources=tuple(SOURCES), targets=tuple(LANGS))
        if result.get("added"):
            self.translator.clear_cache()
            self.hub.publish("memory", self.memory.state())
        return result

    def memory_remove_pack(self, pack: str) -> dict:
        self.memory.remove_pack(pack)
        self.translator.clear_cache()
        self.hub.publish("memory", self.memory.state())
        return {"ok": True}

    def _subtitle_pairs(self, files, swap: bool = False) -> dict:
        """字幕檔（一個雙語的，或原文、翻譯各一個）→ {"ok", "pairs", "lang", "target"} 或 {"ok": False, "error"}。
        target 是 LANGS 的代碼：跟我的語言同一家就用我的語言（繁體或簡體），否則用那一家的第一個。"""
        from .core.translate import SOURCES

        files = [f for f in (files if isinstance(files, list) else []) if isinstance(f, dict) and isinstance(f.get("text"), str)][:2]
        cues = [subtitles.parse(f["text"]) for f in files]
        if not files or not all(cues):
            return {"ok": False, "error": "sub_unreadable"}
        if len(cues) == 2:
            langs = [subtitles.language_of(c, detect_language) for c in cues]
            if not all(langs) or langs[0] == langs[1]:
                return {"ok": False, "error": "sub_same_lang"}
            mine = LANGS[self.cfg["my_lang"]]["family"]
            order = [0, 1] if langs[1] == mine or (langs[0] != mine and len(cues[0]) >= len(cues[1])) else [1, 0]
            if swap:
                order.reverse()
            pairs = subtitles.pair_two(cues[order[0]], cues[order[1]])
            lang, family = langs[order[0]], langs[order[1]]
        else:
            pairs, lang, family = subtitles.split_one(cues[0], detect_language, swap=swap)
        if not pairs:
            return {"ok": False, "error": "sub_no_pairs"}
        mine = self.cfg["my_lang"]
        target = mine if LANGS[mine]["family"] == family else next((k for k, v in LANGS.items() if v["family"] == family), "")
        if lang not in SOURCES or not target:
            return {"ok": False, "error": "sub_lang", "lang": lang or "", "target": family or ""}
        return {"ok": True, "pairs": pairs, "lang": lang, "target": target}

    def subtitles_preview(self, files, swap: bool = False) -> dict:
        """先看看字幕檔配出來的樣子：幾句、前 5 句、哪種語言翻成哪種。也告訴介面現在在看哪一部（可以直接記在那部底下）。"""
        got = self._subtitle_pairs(files, swap)
        if not got["ok"]:
            return got
        now = self._work_seen[3]
        info = self.memory.work_info(now) if now else None
        return {"ok": True, "count": len(got["pairs"]), "sample": [list(p) for p in got["pairs"][:5]], "lang": got["lang"],
                "target": got["target"], "now": {"key": now, "title": info["title"]} if info else None}

    def subtitles_import(self, files, swap: bool = False, work: str = "", title: str = "", name: str = "") -> dict:
        """匯入字幕檔當成翻譯，一包（可以整包刪）。work：記在哪一部底下；沒有就用 title 當新的一部（標題整理過）。"""
        got = self._subtitle_pairs(files, swap)
        if not got["ok"]:
            return got
        if not (work and self.memory.work_info(work)):
            title = works.clean_title(title)
            work = works.work_key(title)
        else:
            title = self.memory.work_info(work)["title"]
        name = re.sub(r"\.(srt|vtt)$", "", str(name or ""), flags=re.I) or title
        result = self.memory.import_subtitles(got["pairs"], got["lang"], got["target"], name, work, title)
        if result.get("ok"):
            self.translator.clear_cache()
            self._work_told.discard(work)
            self.hub.publish("memory", self.memory.state())
        return dict(result, work=work)

    def memory_work(self, key: str, action: str, on: bool = False) -> dict:
        """作品清單上的動作：own_only（這部不用別人的）、forget（忘掉這部）。"""
        if action == "own_only":
            self.memory.set_own_only(key, on)
        elif action == "forget":
            self.memory.forget_work(key)
            self._work_told.discard(key)
            self._work_seen = (0.0, "", "", "")
        else:
            return {"ok": False, "error": "unknown"}
        self.translator.clear_cache()
        self.hub.publish("memory", self.memory.state())
        return {"ok": True}

    def memory_forget(self) -> dict:
        """清除我翻過的句子（教過的留著，那些在「我教過的句子」裡刪）。"""
        n = self.memory.forget_mine()
        self.translator.clear_cache()
        self.hub.publish("memory", self.memory.state())
        return {"ok": True, "removed": n}

    def copy(self, msg_id: int) -> bool:
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        return bool(msg and msg.get("translated") and oswin.copy_text(msg["translated"]))

    # ------------------------------------------------------------------ 設定：匯出、匯入、恢復預設、復原

    def _settings_now(self) -> dict:
        cfg = self.cfg.snapshot()
        return {k: cfg[k] for k in DEFAULTS if k not in SETTINGS_SKIP}

    def settings_export(self) -> dict:
        """把現在的設定存成一個檔案（放在字幕記錄的資料夾），可以備份，或是重灌之後匯入回來。"""
        folder = self.transcript_dir()
        path = folder / f"LiveLingo-settings-{time.strftime('%Y%m%d-%H%M%S')}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"app": "LiveLingo", "kind": SETTINGS_KIND, "version": 1, "app_version": __version__,
                                        "hotkeys_v": HOTKEYS_V, "config": self._settings_now()}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as ex:
            self.log(f"settings export failed: {ex!r}")
            return {"ok": False, "error": str(ex)}
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "name": path.name}

    def settings_backup(self) -> dict | None:
        """上一次匯入或恢復預設之前的設定還留著嗎（可以復原）。回傳 {"at", "reason"} 或 None。"""
        try:
            data = json.loads((self.data / "settings-backup.json").read_text(encoding="utf-8"))
            return {"at": float(data["at"]), "reason": str(data["reason"])}
        except Exception:  # noqa: BLE001
            return None

    def _keep_backup(self, reason: str) -> None:
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            (self.data / "settings-backup.json").write_text(
                json.dumps({"at": time.time(), "reason": reason, "config": self._settings_now()}, ensure_ascii=False), encoding="utf-8")
        except OSError as ex:
            self.log(f"settings backup failed: {ex!r}")

    def _valid_regions(self, sent) -> list[dict] | None:
        """設定檔裡的聊天區（整份換掉用的）：每一個都要有編號和像樣的位置大小，其他欄位整理成固定的樣子。"""
        if not isinstance(sent, list):
            return None
        out: list[dict] = []
        for item in sent[:MAX_REGIONS]:
            try:
                box = {k: int(item[k]) for k in ("left", "top", "width", "height")}
                rid = str(item["id"])[:12]
            except (KeyError, TypeError, ValueError):
                continue
            if not rid or box["width"] < 60 or box["height"] < 30 or any(r["id"] == rid for r in out):
                continue
            anchor = item.get("anchor")
            anchor = [int(anchor[0]), int(anchor[1])] if isinstance(anchor, list) and len(anchor) == 2 and all(
                isinstance(v, (int, float)) for v in anchor) else None
            app = str(item.get("app") or "").strip()[:120] if anchor else ""
            out.append({"id": rid, "name": " ".join(str(item.get("name") or "").split())[:30], **box,
                        "on": bool(item.get("on", True)), "app": app, "anchor": anchor if app else None,
                        **({"story": True} if item.get("story") else {})})
        return out

    @staticmethod
    def _valid_rect(rect) -> list[int] | None:
        if isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, (int, float)) for v in rect) and rect[2] > 0 and rect[3] > 0:
            return [int(v) for v in rect]
        return None

    def _valid_layouts(self, sent) -> list[dict] | None:
        if not isinstance(sent, list):
            return None
        out = []
        for item in sent[:MAX_LAYOUTS]:
            if not isinstance(item, dict) or not isinstance(item.get("look"), dict):
                continue
            name = " ".join(str(item.get("name") or "").split())[:24]
            look = {k: item["look"][k] for k in OVERLAY_LOOK if k in item["look"] and type(item["look"][k]) is type(DEFAULTS[k])}
            rect = self._valid_rect(item.get("rect"))
            if not name or not rect or any(o["name"] == name for o in out):
                continue
            out.append({"name": name, "look": look, "rect": rect,
                        "form": item.get("form") if item.get("form") in OVERLAY_FORMS else "float",
                        "monitor": max(0, min(15, int(item["monitor"]))) if isinstance(item.get("monitor"), int) else 0})
        return out

    def _apply_settings(self, values: dict) -> list[str]:
        """
        把一整份設定套上去（匯入、恢復預設、復原都走這裡）。介面改得到的項目走平常那一套檢查；
        聊天區、留言位置、自己存的版面、字幕資料夾和進階數值另外檢查。不認得或不合格的項目跳過。回傳真的變了的項目。
        """
        before = self.cfg.snapshot()
        self.update_config({k: v for k, v in values.items() if k in EDITABLE and k != "regions"})
        extra: dict = {}
        if "regions" in values:
            regions = self._valid_regions(values["regions"])
            if regions is not None:
                extra["regions"] = regions
        if "overlay_layouts" in values:
            layouts = self._valid_layouts(values["overlay_layouts"])
            if layouts is not None:
                extra["overlay_layouts"] = layouts
        if "overlay_rect" in values:
            rect = self._valid_rect(values["overlay_rect"])
            if rect or values["overlay_rect"] is None:
                extra["overlay_rect"] = rect
        if isinstance(values.get("transcript_dir"), str):
            extra["transcript_dir"] = values["transcript_dir"].strip()[:400]
        for key, (low, high) in ADVANCED_RANGE.items():
            value = values.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
                extra[key] = type(DEFAULTS[key])(value)
        if extra:
            self.cfg.set(**extra)
        after = self.cfg.snapshot()
        changed = sorted(k for k in after if before[k] != after[k])
        if "regions" in changed:
            self._regions_changed()
        if "overlay_rect" in changed:
            self.overlay.reload()
        if "transcript_dir" in changed:
            self.hub.publish("record", self.record_state())
        self.hub.publish("config", after)
        return changed

    def settings_import(self, data) -> dict:
        """匯入設定檔。匯入之前的設定先留一份，可以復原。"""
        if not isinstance(data, dict) or data.get("kind") != SETTINGS_KIND or not isinstance(data.get("config"), dict):
            return {"ok": False, "error": "not_settings"}
        self._keep_backup("import")
        values = {k: v for k, v in data["config"].items() if k in DEFAULTS and k not in SETTINGS_SKIP}
        version = data.get("hotkeys_v", 1)
        if (not isinstance(version, int) or version < HOTKEYS_V) and isinstance(values.get("hotkeys"), dict):
            values["hotkeys"] = migrate_hotkeys(values["hotkeys"])      # 舊版匯出的檔：還是舊預設的快速鍵換成新預設，自己設過的不動
        changed = self._apply_settings(values)
        return {"ok": True, "changed": changed, "count": len(changed), "saved": not self.cfg.error, "backup": self.settings_backup()}

    def settings_reset(self) -> dict:
        """
        恢復預設：設定回到剛安裝的樣子。語言、框好的聊天區、自己存的版面、字幕資料夾留著；
        詞彙表、教過的句子、歷史和下載好的模型本來就不是設定，不會動。之前的設定先留一份，可以復原。
        """
        self._keep_backup("reset")
        values = {k: json.loads(json.dumps(v)) for k, v in DEFAULTS.items() if k not in SETTINGS_SKIP and k not in RESET_KEEP}
        changed = self._apply_settings(values)
        return {"ok": True, "changed": changed, "count": len(changed), "saved": not self.cfg.error, "backup": self.settings_backup()}

    def settings_undo(self, forget: bool = False) -> dict:
        """復原上一次匯入或恢復預設（forget：不復原，只是把留著的那一份丟掉）。"""
        path = self.data / "settings-backup.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            values = data["config"]
            assert isinstance(values, dict)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "no_backup"}
        changed = [] if forget else self._apply_settings({k: v for k, v in values.items() if k in DEFAULTS and k not in SETTINGS_SKIP})
        path.unlink(missing_ok=True)
        return {"ok": True, "changed": changed, "count": len(changed), "saved": not self.cfg.error, "backup": None}

    # ------------------------------------------------------------------ 健康檢查

    def health(self) -> dict:
        """
        健康檢查：翻譯引擎、顯示卡、麥克風、耳機（電腦播放的聲音）、聊天區、快速鍵、設定存檔，各一個燈。
        state：ok 綠 / warn 黃（能用，但有該知道的事）/ bad 紅（現在不能用）/ off 灰（沒在用）。
        code 由介面翻成文字（h_代碼），其他欄位是那句話裡要填的東西；tab 是可以去哪一頁處理。
        只看現有的狀態和裝置清單，不會打開麥克風，也不會動到任何設定。
        """
        items: list[dict] = []

        def add(light: str, state: str, code: str, tab: str, **more) -> None:
            items.append({"id": light, "state": state, "code": code, "tab": tab, **more})

        # 翻譯引擎
        if self.phase == "ready":
            add("engine", "warn" if self.fallback else "ok", "engine_fallback" if self.fallback else "engine_ok", "quality",
                quality=self.mode.get("quality", "standard"), engine=self.mode.get("engine", "cpu"))
        elif self.phase == "need_download":
            add("engine", "bad", "engine_download", "quality", mb=self.detail.get("total_mb", 0))
        elif self.phase == "error":
            add("engine", "bad", "engine_error", "quality", error=str(self.detail.get("message", ""))[:200])
        else:
            add("engine", "warn", "engine_busy", "quality")

        # 顯示卡
        if not self.gpu.get("name"):
            add("gpu", "warn", "gpu_none", "quality")
        else:
            add("gpu", "ok" if self.gpu.get("tier") == "high" else "warn", "gpu_ok" if self.gpu.get("tier") == "high" else "gpu_small",
                "quality", name=self.gpu["name"], gb=round(self.gpu.get("vram_mb", 0) / 1024, 1))

        # 麥克風、耳機
        found = self.devices()
        for name, source, group, chosen_key in (("mic", "mic", "microphones", "mic_device"), ("listen", "listen", "speakers", "speaker_device")):
            src, rows, chosen = self.sources[source], found.get(group) or [], self.cfg[chosen_key]
            using = next((d for d in rows if d["id"] == chosen), None) or next((d for d in rows if d.get("default")), None)
            label = src.get("device") or (using or {}).get("name", "")
            if not found.get("ok"):
                add(name, "bad", "audio_error", "audio", error=str(found.get("error", ""))[:200])
            elif not rows:
                add(name, "bad", f"{name}_none", "audio")
            elif src.get("error"):
                add(name, "bad", f"{name}_error", "audio", error=str(src["error"])[:200])
            elif src.get("lost"):
                add(name, "warn", f"{name}_lost", "audio")
            elif name == "mic" and (src.get("quiet") == "blocked" or oswin.mic_privacy_blocked()):
                add(name, "bad", "mic_blocked", "audio")
            elif name == "mic" and src.get("quiet") == "silent":
                add(name, "warn", "mic_silent", "audio", name=label)
            elif chosen and not any(d["id"] == chosen for d in rows):
                add(name, "warn", f"{name}_missing", "audio", name=label)
            elif name == "listen" and self.cfg["listen_apps"] and self._apps_audio_off:
                add(name, "warn", "listen_apps_off", "audio", name=label)
            else:
                add(name, "ok", f"{name}_ok" if src.get("on") else f"{name}_found", "audio", name=label)

        # 聊天區
        regions, on = list(self.cfg["regions"]), self._regions_on()
        closed = [r for r in on if r.get("app") and self._locate(r) is None]
        if self.sources["screen"].get("error"):
            add("screen", "bad", "screen_error", "screen", error=str(self.sources["screen"]["error"])[:200])
        elif not regions:
            add("screen", "off", "screen_none", "screen")
        elif not on or not self.sources["screen"]["on"]:
            add("screen", "off", "screen_off", "screen", n=len(regions))
        elif closed:
            add("screen", "warn", "screen_closed", "screen", name=apps.display_name(closed[0]["app"]))
        elif self._black_regions & {str(r.get("id") or "") for r in on}:
            add("screen", "warn", "screen_black", "screen")
        else:
            stats = self.watcher.stats() if self.watcher else {}
            add("screen", "ok", "screen_ok", "screen", n=len(on), lines=sum(int(v.get("lines", 0)) for v in stats.values()))

        # 快速鍵
        hk = self.hotkeys_state()
        taken = [name for name, state in hk["states"].items() if state == "taken"]
        if not hk["available"] or not any(self.cfg["hotkeys"].values()):
            add("hotkeys", "off", "hotkeys_off" if hk["available"] else "hotkeys_none", "general")
        elif taken:
            add("hotkeys", "warn", "hotkeys_taken", "general", n=len(taken))
        else:
            add("hotkeys", "ok", "hotkeys_ok", "general", n=sum(1 for s in hk["states"].values() if s == "ok"))

        # 設定存檔
        if self.cfg.error:
            add("saving", "bad", "saving_error", "general", error=str(self.cfg.error)[:200])
        elif self.history.error:
            add("saving", "warn", "saving_history", "history", error=str(self.history.error)[:200])
        else:
            add("saving", "ok", "saving_ok", "general", folder=str(self.data))

        order = ("bad", "warn", "ok", "off")
        worst = min((i["state"] for i in items), key=order.index)
        return {"ok": True, "worst": worst, "items": items, "at": time.time()}

    # ------------------------------------------------------------------ 說明

    def help_state(self) -> dict:
        """說明頁的內容（現在的介面語言、現在設定的快速鍵）。friend_lang：給朋友的那一頁預設用對方的語言。"""
        values = {**self.cfg["hotkeys"], "ptt": self.cfg["ptt_key"], "version": __version__, "download": guide.DOWNLOAD}
        return {"ok": True, **guide.page(self.cfg["ui_lang"], values), "langs": list(UI_LANGS),
                "friend_lang": self.cfg["their_lang"] if self.cfg["their_lang"] in UI_LANGS else self.cfg["ui_lang"]}

    def help_friend(self, lang: str) -> dict:
        """把給朋友的快速上手存成一個 HTML 檔（放在字幕記錄的資料夾），存好把資料夾打開。只是存檔，不會傳給任何人。"""
        if lang not in UI_LANGS:
            return {"ok": False, "error": "help_failed", "detail": ""}
        folder = self.transcript_dir()
        values = {**DEFAULTS["hotkeys"], "ptt": DEFAULTS["ptt_key"], "version": __version__, "download": guide.DOWNLOAD}      # 朋友是新裝的：預設快速鍵
        try:
            path = guide.save_friend(folder, lang, values)
        except OSError as ex:
            self.log(f"quick start page could not be saved: {ex!r}")
            return {"ok": False, "error": "help_failed", "detail": str(ex)}
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "name": path.name, "folder": str(folder), "lang": lang}

    # ------------------------------------------------------------------ 問題報告

    def _report_sections(self, settings: bool, log: bool) -> tuple[list, int]:
        """問題報告的每一段。只有狀態和數字：對話內容、歷史、詞彙表、教過的句子都不放（report.py 開頭有寫）。"""
        health = self.health()
        state = self.storage_state()
        last = updater.Attempts(self.data).read()
        update = self.update_state()
        app = {
            "version": __version__, "installed": paths.FROZEN, "running_minutes": round((time.time() - self.started) / 60),
            "phase": self.phase, "quality": self.mode.get("quality"), "engine": self.mode.get("engine"), "model": self.mode.get("model"),
            "fell_back_to_standard": self.fallback, "window": type(self.window).__name__ if self.window else "none",
            "interface_language": self.cfg["ui_lang"], "my_language": self.cfg["my_lang"], "their_language": self.cfg["their_lang"],
            "auto_detect": self.cfg["their_auto"], "messages_this_session": len(self.messages),
            "history_count": self.history.count, "history_error": self.history.error or None,
            "word_list_count": len(self.glossary.list()), "taught_sentences_count": len(self.lessons.list()),
            "remembered_voices_count": voicebook.count(self.data),      # 只有幾個，沒有名字也沒有聲紋
            "settings_file_error": self.cfg.error or None,
            "previous_version": (update["previous"] or {}).get("version"), "skipped_update": update["skipped"] or None,
            "last_update": {k: last[k] for k in ("version", "from", "rollback") if k in last} or None,
        }
        if self.phase == "error":
            app["error"] = str(self.detail.get("message", ""))[:300]
        machine = report.computer()
        machine.update({"gpu": self.gpu.get("name"), "gpu_memory_gb": round(self.gpu.get("vram_mb", 0) / 1024, 1), "gpu_tier": self.gpu.get("tier"),
                        "screens": [f"{m['width']}x{m['height']} at {m['left']},{m['top']}" + (" (main)" if m["primary"] else "")
                                    for m in oswin.monitors()]})
        lights = []
        for item in health["items"]:
            more = " ".join(f"{k}={item[k]}" for k in item if k not in ("id", "state", "code", "tab"))
            lights.append(f"[{item['state']}] {item['id']}: {item['code']} {more}".rstrip())
        sources = {}
        for name, src in self.sources.items():
            sources[name] = {"on": src["on"], "device": src["device"] or None, "error": src["error"] or None, "quiet": src["quiet"] or None,
                             **({"chosen_programs_only": src["apps"]} if name == "listen" else {})}
        if self.delays:
            sources["voice_seconds"] = {"recognise": round(sum(d[0] for d in self.delays) / len(self.delays), 2),
                                        "translate": round(sum(d[1] for d in self.delays) / len(self.delays), 2), "sentences": len(self.delays)}
        sources["speech_on_graphics_card"] = {k: self.speech_gpu[k] for k in ("state", "active", "error") if self.speech_gpu[k] not in ("", None)}
        hk = self.hotkeys_state()
        sources["hotkeys"] = {"available": hk["available"], **hk["states"]}
        store = {"moved_to_another_folder": state["moved"], "folder": state["folder"], "lost_folder": state["lost"] or None,
                 "free_gb": round(state["free"] / 1e9, 1)}
        for row in state["items"]:
            store[row["id"]] = f"{round(row['bytes'] / 1e6, 1)} MB" + ("" if row["used"] else " (not in use)")
        used = self.usage.summary()
        numbers = {name: {k: v for k, v in used[key].items() if k == "count" or v} for name, key in (("today", "today"), ("last_7_days", "week"), ("last_30_days", "month"))}
        numbers["recorded"] = used["keep"]
        sections: list = [("app", app), ("computer", machine), ("health", lights), ("sources", sources), ("storage", store), ("usage", numbers)]
        dropped = 0
        if settings:
            cfg = self._settings_now()
            cfg["regions"] = [{k: r.get(k) for k in ("id", "left", "top", "width", "height", "on", "app")} for r in cfg["regions"]]      # 取的名字不放
            cfg["overlay_layouts"] = len(cfg["overlay_layouts"])
            sections.append(("settings", cfg))
        if log:
            with self._lock:
                said = [m.get(k) or "" for m in self.messages for k in ("original", "translated", "back")]
            for row in self.history.search(limit=200)["items"]:
                said += [row["original"], row["translated"]]
            rows, dropped = report.drop_said(report.tail(self.data / "app.log", report.LOG_LINES), said)
            engine_rows, more = report.drop_said(report.tail(self.data / "engine.log", report.ENGINE_LINES), said)
            dropped += more
            sections += [("log", rows), ("engine_log", engine_rows)]
            # 引擎用到一半自己停過：它停之前最後寫了什麼（自動重開時留下來的那一份）
            stopped, more = report.drop_said(report.tail(self.data / "engine-stopped.log", report.ENGINE_LINES), said)
            dropped += more
            if stopped:
                sections.append(("engine_stopped", stopped))
        return sections, dropped

    def report_make(self, note: str = "", settings: bool = True, log: bool = True) -> dict:
        """做一份問題報告給使用者看。只是做出來放在記憶體裡：不存檔、不送出，要存要複製都等使用者按。"""
        try:
            sections, dropped = self._report_sections(bool(settings), bool(log))
            text = report.build(self.cfg["ui_lang"], sections, note=note, dropped=dropped)
        except Exception as ex:  # noqa: BLE001  （報告是出問題的時候用的，自己不能再出問題）
            self.log(traceback.format_exc())
            text = report.build(self.cfg["ui_lang"], [("app", {"version": __version__, "report_error": repr(ex)}),
                                                      ("log", report.tail(self.data / "app.log", 60))], note=note)
        self._report = text
        return {"ok": True, "text": text, "bytes": len(text.encode("utf-8")), "lines": text.count("\n")}

    def report_save(self) -> dict:
        """把剛才給使用者看過的那一份存成文字檔（放在字幕記錄的資料夾），存好把資料夾打開。存的和看到的一字不差。"""
        if not self._report:
            return {"ok": False, "error": "report_none"}
        folder = self.transcript_dir()
        try:
            path = report.save(folder, self._report)
        except OSError as ex:
            self.log(f"problem report could not be saved: {ex!r}")
            return {"ok": False, "error": "report_failed", "detail": str(ex)}
        self.log(f"problem report saved: {path.name}")
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "name": path.name, "folder": str(folder)}

    def report_copy(self) -> dict:
        if not self._report:
            return {"ok": False, "error": "report_none"}
        return {"ok": bool(oswin.copy_text(self._report))}

    # ------------------------------------------------------------------ 對話歷史

    def history_export(self, q: str = "") -> dict:
        """把（符合搜尋的）歷史存成文字檔，放在字幕記錄的資料夾，存好把資料夾打開。"""
        folder = self.transcript_dir()
        try:
            path, count = self.history.export(folder, self.cfg["ui_lang"], q)
        except OSError as ex:
            self.log(f"history export failed: {ex!r}")
            return {"ok": False, "error": str(ex)}
        if not path:
            return {"ok": False, "error": "empty"}
        oswin.open_path(folder)
        return {"ok": True, "path": str(path), "name": path.name, "count": count}

    def history_clear(self) -> dict:
        removed = self.history.clear()
        state = self.history.state()
        self.hub.publish("history", state)
        return {"ok": not state["error"], "removed": removed, "error": state["error"]}

    # ------------------------------------------------------------------ 儲存空間

    def _storage_used(self) -> dict:
        """現在要用到哪些大檔案（這些不能刪）：正在用的，加上設定本來要用的（暫時退回標準模式時，高品質的模型還是要留著）。"""
        modes = [m for m in (self.mode, gpu.choose_mode(self.cfg["quality"], self.gpu) if self.gpu else None) if m]
        speech_gpu = self.cfg["speech_device"] == "gpu"      # 語音辨識用顯示卡：要 cuDNN，也要顯示卡版引擎帶來的 CUDA 檔案
        return {"models": {m["model"] for m in modes}, "engines": {m["engine"] for m in modes} | ({"cuda"} if speech_gpu else set()),
                "cudnn": speech_gpu, "updating": self.phase == "updating", "voice": bool(self.cfg["tell_speakers"])}

    def storage_state(self) -> dict:
        """大小都是位元組。lost：設定裡記的資料夾找不到了（外接硬碟沒插？），暫時用回原本的位置。"""
        rows = storage.items(self.data, self.store, self._storage_used())
        saved = self.cfg["store_dir"]
        return {"folder": str(self.store), "default": str(self.data), "moved": self.store != self.data,
                "lost": saved if saved and self.store == self.data else "",
                "free": storage.free_bytes(self.store), "total": sum(r["bytes"] for r in rows), "items": rows,
                "moving": dict(self._move) if self._move else None,
                "can_pick": bool(self.window and hasattr(self.window, "choose_folder"))}

    def _storage_changed(self) -> None:
        self.hub.publish("storage", self.storage_state())

    def storage_choose(self) -> dict:
        """用 Windows 的視窗選要搬去哪個資料夾，選好就開始搬。"""
        if not self.window or not hasattr(self.window, "choose_folder"):
            return {"ok": False, "error": "store_no_window"}
        picked = self.window.choose_folder("")
        return self.storage_move(picked) if picked else {"ok": False, "error": ""}

    def storage_move(self, folder: str = "", back: bool = False) -> dict:
        """把大檔案搬到 folder 裡的 LiveLingo 資料夾（back：搬回原本的位置）。
        先複製，全部複製好才換過去、再刪舊的，所以搬的時候照常可以用，中途取消或失敗也不會少東西。"""
        with self._move_lock:
            if self._move or self.phase not in ("ready", "need_download", "error"):
                return {"ok": False, "error": "store_busy"}
            folder = str(folder or "").strip()
            if not back and not (folder and Path(folder).is_absolute()):
                return {"ok": False, "error": "store_not_writable"}
            target = self.data if back else storage.target_for(Path(folder))
            error, more = storage.check_target(self.store, target)
            if error:
                return {"ok": False, "error": error, **more}
            self._move_cancel.clear()
            self._move = {"to": str(target), "done": 0, "total": 0}
        threading.Thread(target=self._storage_move_work, args=(target,), daemon=True, name="storage-move").start()
        self._storage_changed()
        return {"ok": True, "to": str(target)}

    def storage_cancel(self) -> dict:
        self._move_cancel.set()
        return {"ok": bool(self._move)}

    def _storage_move_work(self, target: Path) -> None:
        old, last = self.store, [0.0]

        def progress(done: int, total: int) -> None:
            self._move.update(done=done, total=total)
            if time.time() - last[0] > 0.4:
                last[0] = time.time()
                self.hub.publish("storage_move", dict(self._move))

        def stop(code: str, **more) -> None:
            self.notice(code, **more)      # 先說結果，再把「正在搬」拿掉：等 _move 變空的人（測試、下一個動作）一定已經看得到結果
            self._move = None
            self._storage_changed()

        try:
            storage.copy_parts(old, target, progress, self._move_cancel.is_set, mark=target != self.data)
        except storage.Cancelled:
            return stop("store_cancelled")
        except Exception as ex:  # noqa: BLE001  磁碟滿了、隨身碟被拔掉…：複製到一半的已經清掉，原本的沒動
            self.log(f"moving the large files to {target} failed: {ex!r}")
            return stop("store_failed", error=str(ex))
        if not self.cfg.set(store_dir="" if target == self.data else str(target)):      # 新位置記不下來：當作沒搬
            if target != self.data:
                storage.remove_parts(target)
            return stop("store_failed", error="config")
        self.store = target
        self.log(f"large files moved from {old} to {target}")
        if self.phase == "ready":      # 翻譯引擎還開著舊位置的模型：重開，換成讀新位置（幾秒鐘）
            self._start_engine()
        left = storage.remove_parts(old)      # 還開著的檔案（Windows 上刪不掉）留到下次開程式再刪
        self.cfg.set(store_leftover=[str(old)] if left else [])
        stop("store_moved_later" if left else "store_moved", folder=str(target))

    def _storage_cleanup(self) -> None:
        """上次搬完刪不掉的舊檔案（那時候還開著），現在刪。只碰有記號檔的資料夾，或原本的資料夾。"""
        left = []
        for name in self.cfg["store_leftover"] or []:
            p = Path(str(name))
            if p == self.store or not (p == self.data or (p / storage.MARK).exists()):
                continue
            if storage.remove_parts(p):
                left.append(str(p))
        if left != list(self.cfg["store_leftover"] or []):
            self.cfg.set(store_leftover=left)

    def _installers_cleanup(self) -> None:
        """新版開起來之後刪掉用過的安裝檔。剛更新完時安裝程式還沒結束，它自己那個檔刪不掉：過一下再試兩次，還不行就等下次開。"""
        try:
            freed, left = updater.tidy(self.data)
        except Exception as ex:  # noqa: BLE001  （清不掉不能讓程式開不起來）
            self.log(f"could not remove used installers: {ex!r}")
            return
        if freed:
            self.log(f"removed used installers: {freed // (1 << 20)} MB")
        if not left:
            return

        def later():
            nonlocal left
            for wait in INSTALLER_RETRY:
                if self.closing.wait(wait) or not left:
                    return
                if self.phase == "updating":      # 正在更新或退回：update 資料夾現在不能碰
                    continue
                freed, left = updater.tidy(self.data, only=left)
                if freed:
                    self.log(f"removed used installers: {freed // (1 << 20)} MB")
                    self._storage_changed()

        threading.Thread(target=later, daemon=True, name="installers").start()

    def storage_remove(self, item_id: str) -> dict:
        """刪掉一樣沒在用的東西（另一個品質的模型、另一種引擎、下載過的更新檔、沒下載完的檔案）。"""
        if self._move or self.phase in ("downloading", "loading"):
            return {"ok": False, "error": "store_busy"}
        row = next((r for r in storage.items(self.data, self.store, self._storage_used()) if r["id"] == item_id), None)
        if not row or not row["removable"]:
            return {"ok": False, "error": "store_in_use"}
        ok = storage.remove_item(self.data, self.store, item_id)
        self.log(f"storage: removed {item_id} ({row['bytes']} bytes), clean={ok}")
        self._storage_changed()
        return {"ok": ok, "freed": row["bytes"], "error": "" if ok else "store_remove_failed"}

    def storage_open(self) -> dict:
        oswin.open_path(self.store)
        return {"ok": True}

    def logs_open(self) -> dict:
        """出錯畫面上的「打開資料夾」：紀錄檔（app.log、engine.log）放的那個資料夾。"""
        return {"ok": bool(oswin.open_path(self.data))}

    # ------------------------------------------------------------------ 全域快速鍵、快速輸入框

    def hotkeys_state(self) -> dict:
        """available：這台電腦能不能用全域快速鍵；states：每個動作 ok（可以用）/ taken（被別的程式佔用）/ off（沒設定）。"""
        return {"available": self.hotkeys.available, "states": dict(self.hotkeys.states),
                "defaults": dict(DEFAULTS["hotkeys"])}

    def start_hotkeys(self) -> None:
        try:
            self.hotkeys.start()
            self.hotkeys.set(self.cfg["hotkeys"])
        except Exception as ex:  # noqa: BLE001  （快速鍵登記不了，其他功能照常）
            self.log(f"hotkeys failed: {ex!r}")

    def pause_hotkeys(self, on: bool) -> None:
        """設定頁在等使用者按新的組合時先停用（最多 30 秒，介面沒回來也會自己恢復）。"""
        self.hotkeys.pause(bool(on))
        if on:
            timer = threading.Timer(30, lambda: self.hotkeys.pause(False))
            timer.daemon = True
            timer.start()

    def _clean_hotkeys(self, sent) -> dict | None:
        if not isinstance(sent, dict):
            return None
        now = {name: hotkey_normalize(self.cfg["hotkeys"].get(name, "")) for name in HOTKEY_ACTIONS}
        keys = dict(now)
        for name, combo in sent.items():
            if name not in HOTKEY_ACTIONS:
                continue
            combo = str(combo or "").strip()
            fixed = hotkey_normalize(combo)
            if combo and not fixed:
                self.notice("hotkey_invalid")
                continue
            keys[name] = fixed
        for name in sent:      # 兩個動作不能用同一組鍵：撞到的那一個不改（整組一起送來、互換的不算撞到）
            if name in keys and keys[name] and any(other != name and keys[other] == keys[name] for other in keys):
                keys[name] = now[name]
                self.notice("hotkey_same")
        return keys

    def _note(self, key: str, **params) -> None:
        """按了快速鍵之後的短提示：顯示在浮動留言上（人在遊戲裡），主視窗也跳一則。"""
        table = HOTKEY_NOTES.get(self.cfg["ui_lang"], HOTKEY_NOTES["en"])
        text = table[key].format(**params) if params else table[key]
        self.overlay.note(text)
        self.hub.publish("notice", {"code": "hotkey_note", "text": text})

    def _hotkey(self, name: str) -> None:
        """快速鍵被按下（在快速鍵那條執行緒上）。只做很快的事，慢的另外開執行緒。"""
        if name == "quick":
            self.quick_input()      # 要在收到快速鍵的這條執行緒上開，Windows 才會讓輸入框跑到最前面
        elif name == "reply":
            self.reply_box()        # 同上：小框要能跑到遊戲前面
        elif name == "phrase":
            self.phrase_box()       # 同上
        elif name == "snap":
            self.snap_text()        # 同上：框選畫面要能跑到遊戲前面
        elif name == "pick":
            threading.Thread(target=self.pick_text, daemon=True, name="pick").start()      # 可能要等一下 Ctrl+C
        elif name == "clipimg":
            threading.Thread(target=self.clipboard_translate, daemon=True, name="clipimg").start()
        elif name == "keys":
            self.show_keys()
        else:
            threading.Thread(target=self._hotkey_work, args=(name,), daemon=True, name="hotkey").start()

    def _hotkey_work(self, name: str) -> None:
        try:
            if name == "overlay":
                self.update_config({"overlay": not self.cfg["overlay"]})
            elif name == "mic":
                if self.phase != "ready":
                    self._note("not_ready")
                elif not self.sources["mic"]["busy"]:
                    turn_on = not self.sources["mic"]["on"]
                    self.set_source("mic", turn_on)
                    self._note("mic_on" if turn_on else "mic_off")
            elif name == "copy":
                self._note("copied" if self.copy_last() else "nothing")
            elif name == "inplace":
                self.update_config({"inplace": not self.cfg["inplace"]})
                self._note("inplace_on" if self.cfg["inplace"] else "inplace_off")
            elif name == "private":
                self.set_private(not self.private)
                self._note("private_on" if self.private else "private_off")
            elif name == "mark":
                self.mark_now()
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())

    def copy_last(self) -> bool:
        """複製我上一句（打的或說的）翻好的話。沒有的話回傳 False。"""
        with self._lock:
            msg = next((m for m in reversed(self.messages) if m["who"] == "me" and m.get("translated")
                        and not m.get("error")), None)
        if not msg or not oswin.copy_text(msg["translated"]):
            return False
        with self._lock:
            msg["copied"] = True
        self.hub.publish("message", dict(msg))      # 只更新畫面上的「已複製」，不要在浮動留言上再跳一次
        return True

    def quick_input(self) -> bool:
        """
        叫出快速輸入框（另外開的小程式）。打完按 Enter：當成我打的字送去翻譯，翻好會自動複製；輸入框自己消失。
        只收使用者在那個框裡打的字，不會替他在別的程式裡打字或貼上。已經開著就不再開一個。
        """
        if self._quick is not None and self._quick.poll() is None:
            return False
        if self.phase != "ready":
            self._note("not_ready")
            return False
        fake = os.environ.get("LIVELINGO_FAKE_QUICK")      # 測試用：不開輸入框，當成打了這句
        if fake:
            self.submit("typed", fake)
            return True
        out = self.data / "quick.json"
        try:
            out.unlink(missing_ok=True)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            child = subprocess.Popen([*self_command(), "--quick-input", str(out), "--lang", self.cfg["ui_lang"]],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=flags)
        except OSError as ex:
            self.log(f"quick input could not start: {ex!r}")
            return False
        self._quick = child
        oswin.allow_foreground(child.pid)

        def wait():
            try:
                child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                child.kill()
            try:
                if out.exists():
                    text = str(json.loads(out.read_text(encoding="utf-8")).get("text", ""))
                    out.unlink(missing_ok=True)
                    self.submit("typed", text[:MAX_TEXT])
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

        threading.Thread(target=wait, daemon=True, name="quick-input").start()
        return True

    # ------------------------------------------------------------------ 回話建議（2.9.0）

    def _reply_target(self, msg_id: int | None) -> dict | None:
        """要幫哪一句想回話：指定的那一則，不然就是對方最近一句翻好的。"""
        with self._lock:
            pool = [m for m in self.messages if not m.get("pending") and not m.get("error") and m.get("translated")]
        if msg_id is not None:
            return next((m for m in pool if m["id"] == msg_id and m.get("who") == "them"), None)
        theirs = [m for m in pool if m.get("who") == "them"]
        return theirs[-1] if theirs else None

    def _reply_lang(self, msg: dict) -> str:
        """回話用哪個語言寫：對方那句是什麼語言就用什麼（跟設定的對方語言同一種時用設定的寫法，例如繁簡）。"""
        their = self.cfg["their_lang"]
        family = msg.get("lang") or LANGS[their]["family"]
        if LANGS[their]["family"] == family:
            return their
        return next((code for code, v in LANGS.items() if v["family"] == family), their)

    def replies(self, msg_id: int | None = None, box: Path | None = None) -> dict:
        """幫對方的一句話想 3 句回覆（用對方的語言，附我的語言的意思）。在背景想，好了送 replies 事件；
        box：回話小框在等的檔案，想好了也寫進去。回傳 {"ok", "id"} 或 {"ok": False, "error": not_ready / nothing / busy}。"""
        from .core import assist

        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        target = self._reply_target(msg_id)
        if target is None:
            return {"ok": False, "error": "nothing"}
        if self._replies.get("state") == "thinking":
            return {"ok": False, "error": "busy"}
        with self._lock:
            lines = [m for m in self.messages if m["id"] <= target["id"] and not m.get("error") and not m.get("pending")]
        lines = [{"who": m.get("who"), "original": m.get("original", ""), "translated": m.get("translated", "")} for m in lines]
        lang = self._reply_lang(target)
        self._replies = {"id": target["id"], "state": "thinking", "items": [], "lang": lang}
        self.hub.publish("replies", dict(self._replies))

        def work():
            items: list[dict] = []
            try:
                self._wake_engine()
                out = self.translator.ask_free(assist.reply_prompt(lines, self.cfg["my_lang"], lang, self.cfg["tone"]), 320)
                items = assist.parse_replies(out, self.cfg["my_lang"], their_lang=lang)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            self._replies = dict(self._replies, state="ready" if items else "failed", items=items)
            self.hub.publish("replies", dict(self._replies))
            if box is not None:
                try:
                    box.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        threading.Thread(target=work, daemon=True, name="replies").start()
        return {"ok": True, "id": target["id"]}

    def reply_copy(self, text: str) -> bool:
        """選了一句回話建議：複製起來（使用者自己貼到聊天）。"""
        text = str(text or "").strip()[:MAX_TEXT]
        return bool(text) and oswin.copy_text(text)

    def reply_box(self) -> bool:
        """快速鍵：跳出回話小框（另外開的小程式，先顯示「正在想」），同時在背景想建議；選了就複製。"""
        if self._reply_child is not None and self._reply_child.poll() is None:
            return False
        if self.phase != "ready":
            self._note("not_ready")
            return False
        if self._reply_target(None) is None:
            self._note("reply_nothing")
            return False
        box, out = self.data / "replies-in.json", self.data / "replies-out.json"
        for f in (box, out):
            f.unlink(missing_ok=True)
        started = self.replies(None, box)
        if not started.get("ok"):
            self._note("reply_nothing" if started.get("error") == "nothing" else "not_ready")
            return False
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            child = subprocess.Popen([*self_command(), "--reply-box", str(box), "--reply-out", str(out), "--lang", self.cfg["ui_lang"],
                                      "--their", self._replies.get("lang") or self.cfg["their_lang"]],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as ex:
            self.log(f"reply box could not start: {ex!r}")
            return False
        self._reply_child = child
        oswin.allow_foreground(child.pid)

        def wait():
            try:
                child.wait(timeout=240)
            except subprocess.TimeoutExpired:
                child.kill()
            try:
                if out.exists():
                    text = str(json.loads(out.read_text(encoding="utf-8")).get("text", ""))
                    out.unlink(missing_ok=True)
                    if self.reply_copy(text):
                        self._note("reply_copied")
                box.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

        threading.Thread(target=wait, daemon=True, name="reply-box").start()
        return True

    # ------------------------------------------------------------------ 翻選取的字、框選翻譯、翻譯結果小卡（2.11.0）

    def _card_show(self, items: list[tuple[int, str]]) -> None:
        """為這幾則（訊息編號, 原文）開一張翻譯結果小卡（滑鼠旁邊），翻好一則就更新一次。新的一張會換掉舊的。"""
        if os.environ.get("LIVELINGO_NO_CARD"):      # 測試用：不開小卡
            return
        path = self.data / "card.json"
        card = {"path": path, "order": [i for i, _ in items], "left": len(items),
                "items": {i: {"original": text, "translated": ""} for i, text in items}}
        with self._card_gate:
            self._cards.clear()      # 舊的那張不再等結果（兩張共用一個檔案，晚到的結果不能蓋掉新的）
            for i, _ in items:
                self._cards[i] = card
        self._card_write(card)
        if self._card_child is not None and self._card_child.poll() is None:
            self._card_child.kill()
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._card_child = subprocess.Popen([*self_command(), "--card", str(path), "--lang", self.cfg["ui_lang"],
                                                 "--their", self.cfg["their_lang"]],
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                creationflags=flags)
        except OSError as ex:
            self.log(f"result card could not start: {ex!r}")

    def _card_write(self, card: dict) -> None:
        data = {"items": [dict(card["items"][i]) for i in card["order"]], "done": card["left"] <= 0}
        try:
            tmp = card["path"].with_name(card["path"].name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(card["path"])
        except OSError:
            pass

    def _card_done(self, msg: dict, translated: str | None) -> None:
        """一則翻好了（translated 是 None = 翻不出來）：有小卡在等它就更新。"""
        if not self._cards:
            return
        with self._card_gate:
            card = self._cards.pop(msg["id"], None)
            if card is None:
                return
            card["items"][msg["id"]]["translated"] = translated or ""
            card["left"] -= 1
            self._card_write(card)

    def pick_text(self) -> bool:
        """快速鍵：翻剪貼簿裡的字，結果顯示在滑鼠旁邊的小卡上（也進對話）。
        預設要使用者自己先選取、按 Ctrl+C；打開「先替我複製」就由這裡按一下 Ctrl+C（沒選到東西會把剪貼簿還原）。"""
        if self.phase != "ready":
            self._note("not_ready")
            return False
        text = ""
        if self.cfg["pick_copy"] and oswin.IS_WIN:
            before = oswin.read_clipboard()
            oswin.copy_text(oswin.SENTINEL)
            if oswin.press_copy():
                for _ in range(12):
                    time.sleep(0.05)
                    text = oswin.read_clipboard()
                    if text != oswin.SENTINEL:
                        break
            if text == oswin.SENTINEL or not text:
                text = ""
                oswin.copy_text(before)      # 什麼都沒選到：把使用者原本複製的還給他
        else:
            text = oswin.read_clipboard()
        text = re.sub(r"\s*\n\s*", " ", text.strip())[:MAX_TEXT]      # 選取的文字常在行尾硬換行：接成一句翻
        if not text:
            self._note("pick_empty")
            return False
        with self._card_gate:
            mid = self.submit("screen", text, None, asked=True)
            if mid is not None:
                self._card_show([(mid, text)])
        return mid is not None

    def _snap_shot(self):
        """按下快速鍵的這一刻整個螢幕的畫面（框選畫面出來之前拍，之後從裡面切；測試時換了擷取函式就不拍）。"""
        if getattr(self, "grab", None) is not None:
            return None
        try:
            import mss
            import numpy as np

            with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                mon = sct.monitors[0]
                return np.ascontiguousarray(np.asarray(sct.grab(mon))[:, :, :3]), int(mon["left"]), int(mon["top"])
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())
            return None

    def snap_text(self) -> bool:
        """快速鍵：先拍下現在的畫面，跳出框選，框好就只讀那一框、翻那一次，結果顯示在滑鼠旁邊的小卡上。"""
        if self._snap_busy:
            return False
        if self.phase != "ready":
            self._note("not_ready")
            return False
        shot = self._snap_shot()
        fake = os.environ.get("LIVELINGO_FAKE_REGION")      # 測試用："left,top,width,height"
        child, out = None, self.data / "snap.json"
        if not fake:
            out.unlink(missing_ok=True)
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                child = subprocess.Popen([*self_command(), "--select-region", str(out), "--lang", self.cfg["ui_lang"], "--mode", "snap"],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            except OSError as ex:
                self.log(f"snap selector could not start: {ex!r}")
                return False
            oswin.allow_foreground(child.pid)
        self._snap_busy = True

        def work():
            region = None
            try:
                if fake:
                    left, top, width, height = (int(v) for v in fake.split(","))
                    region = {"left": left, "top": top, "width": width, "height": height}
                else:
                    try:
                        child.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    if out.exists():
                        region = json.loads(out.read_text(encoding="utf-8"))
                        out.unlink(missing_ok=True)
                if region:
                    self._snap_read({k: int(region[k]) for k in ("left", "top", "width", "height")}, shot)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            finally:
                self._snap_busy = False

        threading.Thread(target=work, daemon=True, name="snap").start()
        return True

    def _snap_read(self, region: dict, shot) -> None:
        """讀框起來的那一塊，一段一段翻。"""
        import numpy as np

        if region["width"] < 24 or region["height"] < 12:
            self.notice("region_small")
            return
        if shot is not None:
            full, off_x, off_y = shot
            x1, y1 = max(0, region["left"] - off_x), max(0, region["top"] - off_y)
            bgr = np.ascontiguousarray(full[y1:y1 + region["height"], x1:x1 + region["width"]])
        else:
            grab = getattr(self, "grab", None)
            if grab is None:
                import mss

                with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                    bgr = np.ascontiguousarray(np.asarray(sct.grab(region))[:, :, :3])
            else:
                bgr = np.ascontiguousarray(grab(region))
        if bgr.size == 0:
            self._note("snap_nothing")
            return
        from .core.watcher import blank_covers

        covers = self.capture_mask()
        if covers:
            bgr = blank_covers(bgr, region, covers)
        self._read_image(bgr, card=True)

    def _read_image(self, bgr, card: bool = False) -> int:
        """讀一張圖裡的字，一段一段送去翻（框選翻譯、拖進來的圖、剪貼簿的圖、攝影機都走這裡）。
        card：結果顯示在滑鼠旁邊的小卡（人在遊戲裡）；不然只出現在主視窗的對話裡。回傳送出去幾段。"""
        groups = snap.paragraphs(self._load_ocr().read(bgr))
        if not groups:
            self._note("snap_nothing")
            return 0
        shown: list[tuple[int, str]] = []
        with self._card_gate:
            for text in groups:
                mid = self.submit("screen", text[:MAX_TEXT], None, asked=True)
                if mid is not None:
                    shown.append((mid, text[:MAX_TEXT]))
            if shown and card:
                self._card_show(shown)
        self._album_keep(bgr, shown)
        return len(shown)

    # ------------------------------------------------------------------ 影片、音訊檔產生雙語字幕（2.13.0）

    def filesubs_state(self) -> dict:
        """給介面：現在有沒有在做、做到哪、上一個做完的結果。"""
        job = self._subs_job
        busy = bool(job and job.is_alive())
        return {"busy": busy, "job": dict(job.state) if job else None, "result": self._subs_result,
                "can_pick": bool(self.window and hasattr(self.window, "choose_file")),
                "exts": sorted(filesubs.EXTS), "max_hours": filesubs.MAX_SECONDS // 3600}

    def _subs_tell(self, state: dict) -> None:
        self.hub.publish("filesubs", dict(self.filesubs_state(), job=state))

    def filesubs_pick(self) -> dict:
        """用 Windows 的視窗選影片或音訊檔。回傳 {"ok", "path"}；按取消是 ok 但 path 空的。"""
        if not self.window or not hasattr(self.window, "choose_file"):
            return {"ok": False, "error": "fs_no_window"}
        picked = self.window.choose_file(sorted(filesubs.EXTS))
        return {"ok": True, "path": picked or ""}

    def filesubs_start(self, path: str, lang: str = "", target: str = "", mode: str = "both") -> dict:
        """開始替一個檔案產生字幕。lang：影片裡的人說的語言（語音辨識用的名字，auto = 自動判斷，空 = 對方的語言）；
        target：翻成什麼（預設我的語言）；mode：both 原文＋譯文 / tr 只有譯文 / src 只有原文。回傳 {"ok"} 或 {"ok": False, "error"}。"""
        if self._subs_job is not None and self._subs_job.is_alive():
            return {"ok": False, "error": "fs_busy"}
        path = str(path or "").strip().strip('"')
        if not path or not Path(path).is_absolute() or not Path(path).is_file():
            return {"ok": False, "error": "fs_not_found"}
        if not filesubs.is_media(path):
            return {"ok": False, "error": "fs_not_media"}
        if self.phase != "ready":
            return {"ok": False, "error": "fs_not_ready"}
        if not (self.store / "models" / "whisper" / "model.bin").exists():
            return {"ok": False, "error": "fs_no_model"}
        target = target if target in LANGS else self.cfg["my_lang"]
        if lang == "auto":
            langs = self._auto_families()
        elif lang in VOICE_FAMILIES:
            langs = [lang]
        else:
            langs = [LANGS[self.cfg["their_lang"]]["family"]]
        title = works.clean_title(Path(path).stem)
        key = works.work_key(title)
        if key:
            self.memory.saw(key, title)
        mode = mode if mode in filesubs.MODES else "both"
        label = target if mode != "src" else langs[0]
        self._subs_result = None
        self._voice_off_since = None      # 辨識模型這段時間不要被放掉

        def recognize(audio, wanted):
            recognizer = self._ensure_recognizer()
            recognizer.beam = BEAMS[self.cfg["speech_mode"]]
            return recognizer.transcribe(audio, wanted)

        def translate(text, source, before):
            self._wake_engine()
            return self.translator.translate(text, target, source=source, context=before or None, remember="file", work=key)

        def done(result: dict) -> None:
            self._subs_result = dict(result, name=Path(path).name, work=key, title=title, target=target)
            if result.get("ok") and key:
                self._work_told.discard(key)
                self.hub.publish("memory", self.memory.state())
            self.hub.publish("filesubs", self.filesubs_state())
            self.notice("filesubs_done" if result.get("ok") else "filesubs_failed")

        job = filesubs.Job(path, langs, LANGS[target]["family"], label, mode, recognize, translate, on_state=self._subs_tell,
                           wait=lambda: self.jobs.waiting() > 0, fallback=self.data / "subtitles",
                           log=self.log, on_done=done)
        self._subs_job, self._subs_key = job, key
        job.start()
        self.hub.publish("filesubs", self.filesubs_state())
        return {"ok": True}

    def filesubs_cancel(self) -> dict:
        job = self._subs_job
        if job is not None and job.is_alive():
            job.cancel.set()
        return {"ok": True}

    def filesubs_open(self) -> dict:
        """在檔案總管裡指出做好的字幕檔。"""
        result = self._subs_result or {}
        return {"ok": bool(result.get("path") and oswin.reveal(result["path"]))}

    # ------------------------------------------------------------------ 喊我名字提醒（2.11.0）

    def _mention_of(self, msg: dict, translated: str) -> str:
        """對方的這一句裡有沒有我設的名字或關鍵字（原文、譯文都找）；有的話回傳是哪一個。"""
        if msg.get("who") != "them" or not self.cfg["alert_words"]:
            return ""
        return mention.find((msg.get("original", ""), translated), list(self.cfg["alert_words"]))

    def _alert(self, msg: dict) -> None:
        """有人提到我：響一聲（同一則只響一次，三秒內最多一次）。標出來是畫面和浮動留言各自照 msg["mention"] 做。"""
        if not msg.get("mention") or msg["id"] in self._alerted:
            return
        self._alerted.append(msg["id"])
        now = time.monotonic()
        if self.cfg["alert_sound"] and now - self._alert_at > 3.0:
            self._alert_at = now
            oswin.beep()

    # ------------------------------------------------------------------ 常用句庫（2.11.0）

    def phrases_state(self) -> dict:
        return self.phrases.state(self.cfg["their_lang"])

    def phrase_action(self, action: str, pid: str = "", text: str = "") -> dict:
        """句庫：add（加一句，我的語言）/ starters（放進幾句起手的）/ remove / edit（改譯成現在對方語言的那一句；空的 = 改回機器翻的）。"""
        their = self.cfg["their_lang"]
        if action == "add":
            if self.phrases.add(text, LANGS[self.cfg["my_lang"]]["family"]) is None:
                return {"ok": False, "error": "nothing", **self.phrases_state()}
        elif action == "starters":
            self.phrases.starters(self.cfg["ui_lang"], LANGS[self.cfg["my_lang"]]["family"])
        elif action == "remove":
            self.phrases.remove(str(pid))
        elif action == "edit":
            self.phrases.set_translation(str(pid), their, str(text or ""), fixed=bool(str(text or "").strip()))
        else:
            return {"ok": False, "error": "unknown", **self.phrases_state()}
        self._phrases_fill()
        state = self.phrases_state()
        self.hub.publish("phrases", state)
        self._phrase_box_write()
        return {"ok": True, **state}

    def _phrases_fill(self) -> None:
        """還沒翻成現在對方語言的常用句，在背景一句一句補翻（翻不出來的這一輪就跳過，下次再試）。"""
        if self.phase != "ready" or not self.phrases.missing(self.cfg["their_lang"]):
            return
        with self._phrase_lock:
            if self._phrase_filling:
                return
            self._phrase_filling = True

        def work():
            tried: set[str] = set()
            try:
                while not self.closing.is_set():
                    their = self.cfg["their_lang"]
                    todo = [r for r in self.phrases.missing(their) if r["id"] not in tried]
                    if not todo or self.phase != "ready":
                        break
                    row = todo[0]
                    tried.add(row["id"])
                    try:
                        self._wake_engine()
                        out = self.translator.translate(row["text"], their, row.get("lang") or LANGS[self.cfg["my_lang"]]["family"], [])
                    except Exception:  # noqa: BLE001
                        self.log(traceback.format_exc())
                        continue
                    if out and out.strip() and self.phrases.set_translation(row["id"], their, out):
                        self.hub.publish("phrases", self.phrases_state())
                        self._phrase_box_write()
            finally:
                self._phrase_filling = False

        threading.Thread(target=work, daemon=True, name="phrases").start()

    def _phrase_box_write(self) -> None:
        """常用句庫的小框開著的話，把最新的句子和譯文寫給它。"""
        child = self._phrase_child
        if child is None or child.poll() is not None:
            return
        try:
            (self.data / "phrases-in.json").write_text(json.dumps({"items": self.phrases.listing(self.cfg["their_lang"])}, ensure_ascii=False),
                                                       encoding="utf-8")
        except OSError:
            pass

    def phrase_box(self) -> bool:
        """快速鍵：跳出常用句庫的小框，按 1 到 9 複製那一句翻好的話（使用者自己貼到聊天）。"""
        if self._phrase_child is not None and self._phrase_child.poll() is None:
            return False
        items = self.phrases.listing(self.cfg["their_lang"])
        if not items:
            self._note("phrase_empty")
            return False
        box, out = self.data / "phrases-in.json", self.data / "phrases-out.json"
        for f in (box, out):
            f.unlink(missing_ok=True)
        try:
            box.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            child = subprocess.Popen([*self_command(), "--phrase-box", str(box), "--phrase-out", str(out), "--lang", self.cfg["ui_lang"],
                                      "--their", self.cfg["their_lang"]],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as ex:
            self.log(f"phrase box could not start: {ex!r}")
            return False
        self._phrase_child = child
        oswin.allow_foreground(child.pid)
        self._phrases_fill()

        def wait():
            try:
                child.wait(timeout=240)
            except subprocess.TimeoutExpired:
                child.kill()
            try:
                if out.exists():
                    text = str(json.loads(out.read_text(encoding="utf-8")).get("text", ""))
                    out.unlink(missing_ok=True)
                    if text.strip() and oswin.copy_text(text.strip()[:MAX_TEXT]):
                        self._note("phrase_copied")
                box.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

        threading.Thread(target=wait, daemon=True, name="phrase-box").start()
        return True

    def clear_messages(self) -> None:
        with self._lock:
            self.messages.clear()
            self._live.clear()
            self.pins = []
            self.marks = []
            self._msg_len.clear()
        self.hub.publish("cleared", {})
        self.hub.publish("marks", [])
        self.overlay.clear()
        self.overlay.set_pins([])
        self.stream.clear()

    # ------------------------------------------------------------------ 設定

    def update_config(self, changes: dict) -> dict:
        """改設定：檢查、存檔、馬上套用。回傳 {"saved": 有沒有存進設定檔, "changed": 真的變了的項目}。"""
        changes = {k: v for k, v in changes.items() if k in EDITABLE}
        if changes.get("ui_lang") not in (None, *UI_LANGS):
            changes.pop("ui_lang")
        for key in ("my_lang", "their_lang"):
            if key in changes and changes[key] not in LANGS:
                changes.pop(key)
        if changes.get("theme") not in (None, *THEMES):
            changes.pop("theme")
        if changes.get("accent") not in (None, *ACCENTS):
            changes.pop("accent")
        if changes.get("tone") not in (None, "casual", "polite"):
            changes.pop("tone")
        if changes.get("quality") not in (None, "auto", "standard", "high"):
            changes.pop("quality")
        if "listen_apps" in changes:
            picked = changes["listen_apps"] if isinstance(changes["listen_apps"], list) else []
            names = [str(p).strip()[:120] for p in picked if isinstance(p, str) and str(p).strip()]
            changes["listen_apps"] = list({n.lower(): n for n in names}.values())[:12]
        if "regions" in changes:
            fixed = self._clean_regions(changes["regions"])
            if fixed is None:
                changes.pop("regions")
            else:
                changes["regions"] = fixed
        if "speak_device" in changes and not (changes["speak_device"] is None or isinstance(changes["speak_device"], str)):
            changes.pop("speak_device")
        if "speak_name" in changes:
            changes["speak_name"] = str(changes["speak_name"] or "")[:120]
        if "alert_words" in changes:
            changes["alert_words"] = mention.clean_words(changes["alert_words"])
        if "gamepad_keys" in changes:
            changes["gamepad_keys"] = gamepad_mod.clean(changes["gamepad_keys"], HOTKEY_ACTIONS)
        if changes.get("chat_send_open") not in (None, *CHAT_OPEN_KEYS):
            changes.pop("chat_send_open")
        if changes.get("ear_split") not in (None, *EAR_SPLITS):
            changes.pop("ear_split")
        if changes.get("learn_level") not in (None, *LEARN_LEVELS):
            changes.pop("learn_level")
        if "camera_index" in changes and not (isinstance(changes["camera_index"], int) and not isinstance(changes["camera_index"], bool)
                                              and 0 <= changes["camera_index"] < 10):
            changes.pop("camera_index")
        if "player_notes" in changes:
            changes["player_notes"] = gametools.clean_players(changes["player_notes"])
        if "wall_monitor" in changes and not (isinstance(changes["wall_monitor"], int) and not isinstance(changes["wall_monitor"], bool)
                                              and 0 <= changes["wall_monitor"] < 8):
            changes.pop("wall_monitor")
        if changes.get("unsure_view") not in (None, "mark", "dim", "hide"):
            changes.pop("unsure_view")
        for key in ("keep_words", "name_skip", "abbr_skip"):
            if key in changes:
                changes[key] = keepwords.clean_words(changes[key])
        if "speak_heard_device" in changes and not (changes["speak_heard_device"] is None or isinstance(changes["speak_heard_device"], str)):
            changes.pop("speak_heard_device")
        if "speak_heard_name" in changes:
            changes["speak_heard_name"] = str(changes["speak_heard_name"] or "")[:120]
        if "term_packs" in changes:
            picked = changes["term_packs"] if isinstance(changes["term_packs"], list) else []
            changes["term_packs"] = [name for name in PACKS if name in picked]
        if "auto_langs" in changes:      # 自動偵測的候選語言：只收認得的、照固定順序；全部都選等於沒限制（存成空的）
            picked = changes["auto_langs"] if isinstance(changes["auto_langs"], list) else []
            kept = [f for f in VOICE_FAMILIES if f in picked]
            changes["auto_langs"] = [] if len(kept) == len(VOICE_FAMILIES) else kept
        for key, allowed in (("overlay_family", chatpaint.FAMILIES), ("overlay_align", chatpaint.ALIGNS), ("overlay_grow", chatpaint.GROWS)):
            if key in changes and changes[key] not in allowed:
                changes.pop(key)
        if "overlay_form" in changes and changes["overlay_form"] not in OVERLAY_FORMS:
            changes.pop("overlay_form")
        if "overlay_monitor" in changes:
            try:
                changes["overlay_monitor"] = max(0, min(15, int(changes["overlay_monitor"])))
            except (TypeError, ValueError):
                changes.pop("overlay_monitor")
        if "overlay_color" in changes:
            color = str(changes["overlay_color"]).upper()
            if re.fullmatch(r"#[0-9A-F]{6}", color):
                changes["overlay_color"] = color
            else:
                changes.pop("overlay_color")
        if "overlay_name_colors" in changes:      # 可以只送其中一個名字的顏色，其他的不動
            sent = changes.pop("overlay_name_colors")
            colors = dict(self.cfg["overlay_name_colors"])
            for who, color in (sent.items() if isinstance(sent, dict) else ()):
                if who in DEFAULTS["overlay_name_colors"] and re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
                    colors[who] = str(color).upper()
            changes["overlay_name_colors"] = colors
        if changes.get("speech_mode") not in (None, *BEAMS):
            changes.pop("speech_mode")
        if changes.get("speech_device") not in (None, "cpu", "gpu"):
            changes.pop("speech_device")
        if changes.get("ptt_key") not in (None, *oswin.PTT_KEYS):
            changes.pop("ptt_key")
        if "end_silence" in changes:
            try:
                changes["end_silence"] = round(max(0.3, min(1.5, float(changes["end_silence"]))), 1)
            except (TypeError, ValueError):
                changes.pop("end_silence")
        if "voice_sensitivity" in changes:
            try:
                changes["voice_sensitivity"] = max(1, min(5, int(changes["voice_sensitivity"])))
            except (TypeError, ValueError):
                changes.pop("voice_sensitivity")
        for key, low, high in (("overlay_font", 14, 60), ("overlay_seconds", 0, 120), ("overlay_backdrop", 0, 80),
                               ("overlay_outline", 0, 4), ("overlay_opacity", 30, 100), ("overlay_lines", 0, 12)):
            if key in changes:
                try:
                    changes[key] = max(low, min(high, int(changes[key])))
                except (TypeError, ValueError):
                    changes.pop(key)
        if "engine_rest" in changes:
            try:
                changes["engine_rest"] = int(changes["engine_rest"])
            except (TypeError, ValueError):
                changes.pop("engine_rest")
            else:
                if changes["engine_rest"] not in ENGINE_REST:
                    changes.pop("engine_rest")
        if "history_days" in changes:
            try:
                changes["history_days"] = int(changes["history_days"])
            except (TypeError, ValueError):
                changes.pop("history_days")
            else:
                if changes["history_days"] not in HISTORY_DAYS:
                    changes.pop("history_days")
        if "hotkeys" in changes:      # 可以只送其中一個動作；空字串 = 不用；不能用的組合、跟別的動作撞到的都不收
            fixed = self._clean_hotkeys(changes["hotkeys"])
            if fixed is None:
                changes.pop("hotkeys")
            else:
                changes["hotkeys"] = fixed
        for key in changes:
            if isinstance(DEFAULTS[key], bool):
                changes[key] = bool(changes[key])
        before = self.cfg.snapshot()
        # 兩邊可以選同一種語言（只想要字幕，或是繁體和簡體互轉），不會自動對調
        saved = self.cfg.set(**changes)
        after = self.cfg.snapshot()
        self.hub.publish("config", after)

        def changed(*keys):
            return any(before[k] != after[k] for k in keys)

        if changed("tone"):
            self.translator.tone = after["tone"]
            self.translator.clear_cache()
            self._warm_engine()      # 語氣不一樣，提示的開頭也不一樣
        if changed("tone_keep"):
            self.translator.keep_tone = bool(after["tone_keep"])
            self.translator.clear_cache()
            self._warm_engine()      # 保留語氣的提示不一樣
        if changed("their_lang"):
            self._phrases_fill()      # 句庫裡還沒翻成這個語言的，在背景補翻
            self._phrase_box_write()
        if changed("game_terms", "game_auto"):
            threading.Thread(target=self.check_game, daemon=True).start()
            self._terms_changed()
        if changed("game_start"):
            self._auto_apps = set()      # 打開：現在已經開著的遊戲也算「剛打開」；關掉：之後不再自動開關
            if not after["game_start"]:
                self._auto_started = []
        if changed("use_shown_translation", "my_lang") and self.watcher is not None:
            self.watcher.pair_lang = self._shown_pair_lang()
        if changed("remember_translations", "use_shared"):
            self.memory.mine_on, self.memory.shared_on = bool(after["remember_translations"]), bool(after["use_shared"])
            self.translator.clear_cache()
        if changed("term_packs"):
            self._terms_changed()
        if changed("keep_words"):
            self.translator.clear_cache()      # 譯文裡的字要照原樣留著了：之前翻好的不能再用
        if changed("channel_colors", "combat_slow") and self.watcher:
            self.watcher.colors, self.watcher.slow_busy = bool(after["channel_colors"]), bool(after["combat_slow"])
        if changed("stream", "stream_original", "stream_mine", "overlay_seconds", "overlay_font"):
            self.stream.apply()
            self.hub.publish("stream", self.stream.state())
        if changed("on_top") and self.window:
            self.window.set_on_top(after["on_top"])
        if changed("share_visible"):
            self.overlay.apply_share()
            if self.main_hwnd:
                oswin.exclude_from_capture(self.main_hwnd, not after["share_visible"])
        if changed("overlay", "overlay_form", "overlay_monitor", *OVERLAY_LOOK, "ui_lang", "my_lang", "their_lang"):
            self.overlay.apply()
            if changed("overlay"):
                self.hub.publish("overlay", self.overlay.state())
        if changed("their_auto", "their_lang", "my_lang", "auto_langs"):
            self._their_run = self._new_run()      # 語言是使用者改的：之前聽到一半的不再算
        if changed("hotkeys"):
            self.hotkeys.set(after["hotkeys"])
        if changed("gamepad_on", "gamepad_keys"):
            self._apply_gamepad()
        if changed("overlay_hover_fade"):
            self.overlay.wake()
        if changed("history_keep", "history_days"):
            self.history.configure(after["history_keep"], after["history_days"])
        if changed("usage_keep"):
            self.usage.configure(after["usage_keep"])
            self.hub.publish("history", self.history.state())
        if changed("listen_apps"):
            self._apps_audio_off = False      # 換了選擇：再試一次
            if self.sources["listen"]["on"]:
                self._restart_voice("listen")
        if changed("regions"):
            self._regions_changed()
        if changed("inplace"):
            self._inplace_apply()
        if changed("my_lang", "their_lang", "their_auto", "also_english", "tone", "tone_keep"):
            self.inplace.clear_cache()      # 蓋在畫面上的譯文是舊語言、舊語氣翻的
        if changed("speech_device"):
            threading.Thread(target=self._speech_device_changed, daemon=True).start()
        if changed("tell_speakers"):
            self.speakers_load()
            self._storage_changed()
        if changed("remember_speakers"):
            self._remember_changed()
        if changed("voice_sensitivity", "end_silence", "speech_mode"):      # 收音中直接換，不用重開
            with self._lock:
                for pipe in self.pipelines.values():
                    pipe.segmenter.tune(float(after["end_silence"]), VAD_THRESHOLD[int(after["voice_sensitivity"])])
                if self.recognizer is not None:
                    self.recognizer.beam = BEAMS[after["speech_mode"]]
        if changed("my_lang", "their_lang", "their_auto", "also_english"):
            threading.Thread(target=self._languages_changed, daemon=True).start()
        elif changed("auto_langs"):      # 只是換了候選語言：收音和讀螢幕不用重開，直接換語言清單
            threading.Thread(target=self._apply_langs, daemon=True).start()
        if changed("my_lang", "their_lang"):
            self._warm_engine()
        if changed("speaker_device") and self.sources["listen"]["on"]:
            self._restart_voice("listen")
        if changed("mic_device") and self.sources["mic"]["on"]:
            self._restart_voice("mic")
        if changed("quality") and self.phase in ("ready", "error", "need_download"):
            self.fallback = False
            new_mode = gpu.choose_mode(after["quality"], self.gpu)
            if new_mode != self.mode or self.phase != "ready":
                threading.Thread(target=self._prepare, daemon=True).start()
        return {"saved": saved, "error": self.cfg.error, "changed": sorted(k for k in after if before[k] != after[k])}

    def _languages_changed(self) -> None:
        with self._lock:
            if self.watcher:
                was_on = self.sources["screen"]["on"]
                self._stop_watcher()
                if was_on:
                    self._start_screen()
            for key in ("listen", "mic"):
                if self.sources[key]["on"]:
                    self._restart_voice(key)

    # ------------------------------------------------------------------ 來源：螢幕

    def _screen_langs(self) -> set[str]:
        my, their = LANGS[self.cfg["my_lang"]]["family"], LANGS[self.cfg["their_lang"]]["family"]
        langs = {their}
        if self.cfg["their_auto"]:
            # 自動偵測：同一個文字辨識模型讀得懂的語言都收（中文和日文同一個；西、葡、法、德、印尼、越南同一個）。
            # 用別的模型的語言（例如韓文）要等聲音判斷出來才會換過去
            from .core.ocr import model_of

            model = model_of(their)
            langs |= {f for f in self._auto_families() if model_of(f) == model and f != "en"} - {my}
        if self.cfg["also_english"] and my != "en":
            langs.add("en")
        return langs

    def _load_ocr(self):
        from .core.ocr import OcrEngine, model_of

        family = LANGS[self.cfg["their_lang"]]["family"]
        if self.ocr is None or self.ocr.model != model_of(family):
            self.ocr = OcrEngine(self.store / "models" / "ocr", scale=float(self.cfg["ocr_scale"]), family=family)
        return self.ocr

    def _regions_on(self) -> list[dict]:
        """開著的聊天區。"""
        return [dict(r) for r in self.cfg["regions"] if r.get("on")]

    def _on_screen_line(self, text: str, lang: str, score: float = 1.0, region: dict | None = None, shown: str = "",
                        color: str = "") -> None:
        """shown：雙語字幕裡緊鄰這一行、我的語言的那一行（開著「用畫面上現成的翻譯」才有）。
        color：這一行字在畫面上的顏色（開著「聊天頻道分色」才有）。"""
        if self.voice_paused:      # 語音指令「暫停翻譯」（2.18.0）：畫面上的也先不翻
            return
        many = len(self._regions_on()) > 1      # 只有一個區域時不用標是哪裡讀到的
        if self.cfg["ocr_fix"]:
            fixed = textfix.fix_ocr(text)      # 數字裡的 O、l，韓文子音縮寫裡的 0、7（2.15.0）
            if fixed != text:
                self.log(f"screen text fixed before translating: {text!r} -> {fixed!r}")
                text = fixed
        self.submit("screen", text, lang, unsure=score < 0.8, place=(region or {}).get("name", "") if many else "",
                    **({"shown": shown} if shown else {}), **self._screen_extra(color, region))

    # ------------------------------------------------------------------ 原地覆蓋翻譯（2.13.0）

    def _inplace_wanted(self, text: str) -> str | None:
        from .core.watcher import wanted

        return wanted(text, self._screen_langs())

    def _inplace_translate(self, text: str, lang: str) -> str:
        """蓋在畫面上的那一段：翻成我的語言。引擎還沒好就先不翻（回傳空字串，下一次畫面有變再來）。"""
        if self.phase != "ready" or self.closing.is_set():
            return ""
        self._wake_engine()
        return self.translator.translate(text[:MAX_TEXT], self.cfg["my_lang"], source=lang, remember="screen")

    def _on_frame(self, region: dict, place, bgr, lines) -> bool:
        return self.inplace.update(region, place, bgr, lines)

    def _inplace_apply(self) -> None:
        """「原地覆蓋翻譯」開關改了。要有開著的框選區域才有東西可以蓋；讀螢幕沒開的話一起打開。"""
        on = bool(self.cfg["inplace"])
        if on and not self._regions_on() and not self.cfg["regions"]:
            self.cfg.set(inplace=False)
            self.hub.publish("config", self.cfg.snapshot())
            self._note("inplace_no_region")
            return
        self.inplace.set_on(on)
        if on:
            if self.watcher:
                self.watcher.on_frame = self._on_frame
            if not self.sources["screen"]["on"] and not self.sources["screen"]["busy"]:
                self.set_source("screen", True)

    def _shown_pair_lang(self) -> str | None:
        return LANGS[self.cfg["my_lang"]]["family"] if self.cfg["use_shown_translation"] else None

    def capture_mask(self) -> list[list[int]]:
        """
        LiveLingo 自己蓋在螢幕上的東西（浮動字幕、主視窗）在哪裡。它們現在讓分享螢幕拍得到，
        讀螢幕時也拍得到，所以要先塗黑，不然自己顯示的譯文會被讀進來又翻一次。
        不讓分享拍到的時候（share_visible 關掉）擷取本來就拍不到它們，不用遮。
        """
        if not self.cfg["share_visible"]:
            return []
        out = []
        cover = self.overlay.covers()
        if cover:
            out.append(cover)
        main = oswin.window_rect_of(self.main_hwnd)
        if main:
            out.append(main)
        return out

    def _locate(self, region: dict) -> dict | None:
        """這個區域現在實際在螢幕上的哪裡。跟著程式視窗的：視窗搬到哪就跟到哪，視窗沒開或縮小了就先不讀。"""
        place = {k: int(region[k]) for k in ("left", "top", "width", "height")}
        exe, anchor = region.get("app"), region.get("anchor")
        if not exe or not anchor:
            return place
        rect = apps.window_rect(exe)
        if rect is None:
            return None
        place["left"] += rect[0] - int(anchor[0])
        place["top"] += rect[1] - int(anchor[1])
        return place

    def _clean_regions(self, sent) -> list[dict] | None:
        """
        介面送來的區域清單：只能改名字、開關、跟著哪個程式，或是拿掉某一個；位置和大小只有框選畫面能改。
        要跟著的程式現在沒開（不知道視窗在哪）就不存，回傳 None。
        """
        if not isinstance(sent, list):
            return None
        have = {r["id"]: r for r in self.cfg["regions"]}
        out = []
        for item in sent[:MAX_REGIONS]:
            old = have.get(item.get("id")) if isinstance(item, dict) else None
            if old is None or any(r["id"] == old["id"] for r in out):
                continue
            region = dict(old)
            if isinstance(item.get("name"), str):
                region["name"] = " ".join(item["name"].split())[:30]
            if "on" in item:
                region["on"] = bool(item["on"])
            for flag in ("story", "comic"):      # 劇情對話框：等字打完再翻（2.14.0）；條漫長圖：一個對話框翻一則（2.17.0）
                if flag in item:
                    if item[flag]:
                        region[flag] = True
                    else:
                        region.pop(flag, None)
            if isinstance(item.get("app"), str) and item["app"].strip()[:120] != (old.get("app") or ""):
                exe = item["app"].strip()[:120]
                rect = apps.window_rect(exe) if exe else None
                if exe and rect is None:
                    self.notice("region_app_closed", app=apps.display_name(exe))
                    return None
                region["app"], region["anchor"] = exe, ([rect[0], rect[1]] if rect else None)
            out.append(region)
        return out

    def _regions_changed(self) -> None:
        """區域的清單改了（改名、開關、拿掉、換位置）：正在讀的話直接換，不用重開。"""
        active = self._regions_on()
        if self.watcher:
            self.watcher.set_regions(active)
        self.inplace.keep_only({str(r.get("id") or "") for r in active})
        if not active and (self.sources["screen"]["on"] or self.cfg["sources"].get("screen")):
            self.set_source("screen", False)      # 沒有開著的區域了

    def _start_screen(self) -> None:
        from .core.watcher import ScreenWatcher

        self._source("screen", busy=True, error="")
        try:
            ocr = self._load_ocr()
            if self.watcher is None:
                self.watcher = ScreenWatcher(
                    ocr, on_message=self._on_screen_line, on_status=self._screen_error,
                    interval=float(self.cfg["interval"]), source_langs=self._screen_langs(),
                    grab=getattr(self, "grab", None), locate=self._locate,
                    on_stats=lambda stats: self.hub.publish("screen_stats", stats), on_black=self._screen_black,
                    mask=self.capture_mask)
                self.watcher.pair_lang = self._shown_pair_lang()
                self.watcher.on_frame = self._on_frame
                self.watcher.colors = bool(self.cfg["channel_colors"])
                self.watcher.grouper = snap.paragraphs      # 條漫長圖模式（2.17.0）：一個對話框一則
                self.watcher.slow_busy = bool(self.cfg["combat_slow"])
                self.watcher.set_regions(self._regions_on())
                self.watcher.start()
            self.watcher.paused = False
            self._source("screen", on=True, busy=False)
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self._source("screen", on=False, busy=False, error=str(ex))

    def _screen_black(self, ids: list[str]) -> None:
        """有聊天區抓到的畫面變成全黑（或恢復了）。新變黑的說一聲；健康檢查也會亮黃燈。"""
        new = set(ids) - self._black_regions
        self._black_regions = set(ids)
        if new:
            self.log(f"screen region(s) {sorted(new)} are completely black; exclusive fullscreen?")
            self.notice("screen_black")

    def _screen_error(self, err: str) -> None:
        if self.watcher and self.watcher.failed:
            self.log(f"screen watcher stopped: {err}")
            self.watcher = None
            self._black_regions = set()
            self._source("screen", on=False, busy=False, error=err)
        else:
            self._source("screen", error=err)

    def _stop_watcher(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self._black_regions = set()

    def select_region(self, then_enable: bool = True, rid: str | None = None, new: bool = False) -> None:
        """
        跳出框選畫面（另外開一個小程式），框好之後存起來。
        rid：重框哪一個區域；new：新增一個。都沒給：有區域就重框第一個，沒有就新增。
        """
        if self._selecting:
            return
        if (new or not self.cfg["regions"]) and len(self.cfg["regions"]) >= MAX_REGIONS:
            self.notice("regions_full", max=MAX_REGIONS)
            return
        self._selecting = True

        def work():
            region = None
            try:
                fake = os.environ.get("LIVELINGO_FAKE_REGION")   # 測試用："left,top,width,height"
                if fake:
                    left, top, width, height = (int(v) for v in fake.split(","))
                    region = {"left": left, "top": top, "width": width, "height": height}
                else:
                    out = self.data / "region.json"
                    out.unlink(missing_ok=True)
                    if self.window:
                        self.window.minimize()
                    try:
                        subprocess.run([*self_command(), "--select-region", str(out), "--lang", self.cfg["ui_lang"]],
                                       timeout=300, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                    finally:
                        if self.window:
                            self.window.restore()
                    if out.exists():
                        region = json.loads(out.read_text(encoding="utf-8"))
                        out.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            finally:
                self._selecting = False
            if region and region.get("width", 0) >= 60 and region.get("height", 0) >= 30:
                box = {k: int(region[k]) for k in ("left", "top", "width", "height")}
                regions = [dict(r) for r in self.cfg["regions"]]
                target = None if new else next((r for r in regions if r["id"] == rid), regions[0] if regions and not rid else None)
                if target is None:
                    used = {r["id"] for r in regions}
                    target = {"id": next(f"r{n}" for n in itertools.count(1) if f"r{n}" not in used),
                              "name": "", "on": True, "app": "", "anchor": None}
                    regions.append(target)
                target.update(box)
                if target.get("app"):      # 跟著程式視窗的區域：記下現在視窗在哪，之後視窗搬家就照差多少跟著搬
                    rect = apps.window_rect(target["app"])
                    if rect is None:
                        self.notice("region_app_closed", app=apps.display_name(target["app"]))
                        target["app"], target["anchor"] = "", None
                    else:
                        target["anchor"] = [rect[0], rect[1]]
                if then_enable:
                    target["on"] = True
                ok = self.cfg.set(regions=regions[:MAX_REGIONS])
                self.hub.publish("config", self.cfg.snapshot())
                if ok:
                    self.hub.publish("saved", {"what": "region", "id": target["id"]})
                self._regions_changed()
                if then_enable:
                    self.set_source("screen", True)
            elif region:
                self.notice("region_small")
            self.hub.publish("region_done", {"ok": bool(region)})

        threading.Thread(target=work, daemon=True, name="region").start()

    def ocr_test(self, rid: str | None = None) -> dict:
        """設定頁的「試讀一次」：現在就讀一次框選的區域（rid 沒給就是第一個開著的），回傳讀到的文字。"""
        regions = self.cfg["regions"]
        chosen = next((r for r in regions if r["id"] == rid), None) if rid else next(iter(self._regions_on() or regions), None)
        if not chosen:
            return {"ok": False, "error": "no_region"}
        region = self._locate(chosen)
        if region is None:
            return {"ok": False, "error": "app_closed", "app": apps.display_name(chosen.get("app") or "")}
        try:
            import numpy as np

            from .core.ocr import message_lines

            ocr = self._load_ocr()
            grab = getattr(self, "grab", None)
            if grab is None:
                import mss

                with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                    bgr = np.ascontiguousarray(np.asarray(sct.grab(region))[:, :, :3])
            else:
                bgr = np.ascontiguousarray(grab(region))
            from .core.watcher import SeenLines, blank_covers, join_wrapped, text_edge

            covers = self.capture_mask()
            if covers:
                bgr = blank_covers(bgr, region, covers)

            rows = message_lines(ocr.read(bgr))
            # 跟平常讀的時候一樣把換行的訊息接回一則，這裡看到的才是真的會拿去翻的樣子
            lines = [ln.text for ln, _ in join_wrapped(rows, SeenLines(), text_edge(rows, bgr.shape[1])[1])]
            return {"ok": True, "lines": lines[-12:]}
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            return {"ok": False, "error": str(ex)}

    # ------------------------------------------------------------------ 來源：語音

    def _voice_langs(self, key: str) -> list[str]:
        my, their = LANGS[self.cfg["my_lang"]]["family"], LANGS[self.cfg["their_lang"]]["family"]
        if key == "listen":
            # 自動偵測：在候選的語言裡聽，目前判斷到的排第一個
            return self._auto_families() if self.cfg["their_auto"] else [their]
        return list(dict.fromkeys([my, their]))

    def _auto_families(self) -> list[str]:
        """
        自動偵測時對方可能說的語言（語音辨識用的語言名）：設定裡選了哪些就是哪些，沒選過就是全部，目前的語言排第一個。
        目前的語言和我的語言一定在裡面：少了它們，對方說我的語言時會被硬聽成別的語言。
        """
        my, their = LANGS[self.cfg["my_lang"]]["family"], LANGS[self.cfg["their_lang"]]["family"]
        picked = {f for f in self.cfg["auto_langs"] if f in VOICE_FAMILIES}
        allowed = {*picked, my, their} if picked else set(VOICE_FAMILIES)
        return list(dict.fromkeys([their, *(f for f in VOICE_FAMILIES if f in allowed)]))

    @staticmethod
    def _new_run() -> dict:
        """自動偵測「連續幾句都像同一個別的語言」的紀錄：這串是哪個語言、每句幾個字、什麼時候開始和最近一句、之前丟掉幾句。"""
        return {"family": "", "letters": [], "first": 0.0, "last": 0.0, "ignored": 0}

    def _their_language_heard(self, family: str, text: str) -> None:
        """
        自動偵測：對方這句話聽起來是 family 這個語言。一句話不算數（語音辨識對短句、背景音樂常猜錯），要連續
        SWITCH_RUN 句都像「同一個別的語言」才把「對方用的語言」換過去；中間夾了一句現在的語言（或我的語言）就從頭算。
        太短的句子（嗯、ok、哈哈）看不出是什麼語言：不算，也不打斷。剛換過去的 SWITCH_COOLDOWN 秒內先不再換，
        免得兩邊來回跳。對方說的剛好是我的語言時不換：那句話本來就看得懂，換了反而我說的話沒有東西可以翻。
        """
        my, their = LANGS[self.cfg["my_lang"]]["family"], LANGS[self.cfg["their_lang"]]["family"]
        letters = len(re.sub(r"[\W_]+", "", text))
        if letters < EVIDENCE_MIN[family in ALPHABETIC]:
            return
        run, now = self._their_run, time.monotonic()
        if family in (their, my) or family not in self._auto_families():
            run["ignored"] += len(run["letters"])      # 剛剛那幾句只是偶爾聽錯，不是真的換語言
            run.update(family="", letters=[])
            return
        if run["family"] != family or now - run["last"] > SWITCH_RUN_GAP:
            run["ignored"] += len(run["letters"])
            run.update(family=family, letters=[], first=now)
        run["letters"].append(letters)
        run["last"] = now
        if len(run["letters"]) < SWITCH_RUN or now - self._their_switched_at < SWITCH_COOLDOWN:
            return
        code = family if family in LANGS else ("zh-CN" if self.cfg["ui_lang"] == "zh-CN" else "zh-TW")
        before = self.cfg["their_lang"]
        self.cfg.set(their_lang=code)
        # 紀錄只寫語言代碼和句數，不寫說了什麼
        self.log(f"auto-detect: the other person now speaks {code} (was {before}): {len(run['letters'])} sentences in a row over "
                 f"{now - run['first']:.0f}s ({run['letters']} letters), {run['ignored']} one-off sentences ignored before")
        self._their_run, self._their_switched_at = self._new_run(), now
        self.hub.publish("config", self.cfg.snapshot())
        self.notice("their_switched", lang=code)
        self.overlay.apply()
        threading.Thread(target=self._apply_langs, daemon=True).start()

    def _apply_langs(self) -> None:
        """
        對方的語言（或候選語言）變了：收音和讀螢幕都不停下來，直接把新的語言給正在跑的那幾個。
        以前每換一次，讀螢幕和麥克風都要重開：換得越勤越卡，重開之後畫面上最後幾行還會再翻一次。
        """
        with self._lock:
            for key, pipe in list(self.pipelines.items()):
                pipe.langs = self._voice_langs(key)      # 辨識到一半的那一句已經留了一份清單，不受影響
        watcher = self.watcher
        if watcher is None:
            return
        try:
            watcher.ocr = self._load_ocr()      # 文字辨識模型一樣就是原來那個；不一樣（韓文換英文）才載入新的，載入時照舊讀
            watcher.source_langs = self._screen_langs()
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            with self._lock:
                self._stop_watcher()
            self._source("screen", on=False, busy=False, error=str(ex))

    def _ensure_recognizer(self):
        """語音辨識模型（Whisper）；還沒載入就載入。收音和「影片字幕」共用同一個。模型沒下載會丟出錯誤。"""
        from .core.speech import Recognizer

        with self._lock:
            if self.recognizer is None:
                model_dir = self.store / "models" / "whisper"
                if not (model_dir / "model.bin").exists():
                    raise RuntimeError("the speech model is missing")
                device, compute = self._speech_engine()
                self.recognizer = Recognizer(model_dir, threads=int(self.cfg["whisper_threads"]), device=device,
                                             compute_type=compute, on_fallback=self._speech_gpu_failed)
                self.recognizer.turn = self._cpu_turn
                self.recognizer.hotwords = lambda lang: keepwords.hotwords(lang, self._words_now())
                self.recognizer.cantonese = lambda: bool(self.cfg["cantonese"])
            return self.recognizer

    def _start_voice(self, key: str, again: bool = False) -> None:
        """again：斷線之後自動重新連接的那幾次（失敗了不顯示錯誤，接著等下一次）。"""
        kind = VOICE_KIND[key]
        self._voice_open.pop(key, None)
        self._source(key, busy=True, error="")
        try:
            from .core.speech import SpeechPipeline

            self._voice_off_since = time.monotonic()      # 正在開收音：語音辨識模型先不要被放掉
            recognizer = self._ensure_recognizer()
            device = self.cfg["speaker_device"] if key == "listen" else self.cfg["mic_device"]
            capture = None
            if key == "listen" and self.cfg["listen_apps"] and not self._apps_audio_off and hasattr(self.audio, "capture_apps"):
                capture = self.audio.capture_apps(list(self.cfg["listen_apps"]), on_error=self._apps_audio_error,
                                                  on_open=self._voice_opened)
            self._source(key, apps=bool(capture))      # 介面要知道現在是「只聽指定程式」還是整台電腦
            if capture is None:
                capture = self.audio.capture(kind, device, on_error=self._voice_error, on_open=self._voice_opened)
            recognizer.beam = BEAMS[self.cfg["speech_mode"]]
            if key == "listen":
                self.speakers_load()      # 有開「分辨說話者」的話，模型在背景準備（還沒好之前字幕照出，只是不標是誰）
            pipe = SpeechPipeline(capture, recognizer, self._voice_langs(key),
                                  on_text=self._on_speech, on_state=self._on_speaking,
                                  end_silence=float(self.cfg["end_silence"]), on_level=self._on_level,
                                  threshold=VAD_THRESHOLD[int(self.cfg["voice_sensitivity"])],
                                  gate=self._mic_gate if key == "mic" else None,
                                  # 即時字幕只給「對方說的話」：自己說的反正要等整句才複製，先辨識只會拖慢對方那一邊
                                  on_partial=self._on_live if key == "listen" else None,
                                  live=lambda: bool(self.cfg["live_captions"]), busy=self._live_wait,
                                  # 分辨說話者也只給「對方說的話」
                                  voices=self._voices_now if key == "listen" else None,
                                  # 每個人自己的語言（主語言、副語言）：設過的人照他的語言聽
                                  speaker_langs=self._speaker_lang_table if key == "listen" else None)
            pipe.denoise = lambda: bool(self.cfg["denoise"])      # 背景降噪（2.18.0）
            if key == "listen" and self.cfg["their_auto"]:
                pipe.stay = 1.5
            old = self.pipelines.pop(key, None)
            if old:
                old.stop()
            if key == "mic":
                self._mic_heard = False
            pipe.start()
            self.pipelines[key] = pipe
            self._source(key, on=True, busy=False, quiet="")
            if self.cfg["speech_autopick"] and not float(self.cfg["speech_picked"] or 0) and not self._speed_testing:
                self._speed_testing = True
                threading.Thread(target=self._speed_pick, daemon=True, name="speed").start()
        except Exception as ex:  # noqa: BLE001
            from .core.audio import describe

            self.log(traceback.format_exc())
            if again:
                self._source(key, busy=False)
                return self._voice_error(kind, ex)
            self._source(key, on=False, busy=False, error=describe(ex))

    def _stop_voice(self, key: str) -> None:
        pipe = self.pipelines.pop(key, None)
        if pipe:
            pipe.stop()
        self._end_live("loopback" if key == "listen" else "mic")      # 說到一半就關掉：先顯示的那半句不會有整句來接，收回
        self._vu[key] = 0.0
        self.hub.publish("vu", dict(self._vu))

    def _voice_opened(self, kind: str, name: str) -> None:
        self.log(f"{kind} capture opened: {name}")
        key = SOURCE_OF[kind]
        self._voice_open[key] = True
        back = bool(self._voice_tries.pop(key, 0))
        self._source(key, device=name, lost=False)
        if back:
            self.notice("device_back", name=name)

    def _on_level(self, kind: str, level: float, silent_for: float) -> None:
        """收音管線每收到一批聲音就回報一次：畫音量條，並且注意麥克風是不是一直完全沒聲音。"""
        key = SOURCE_OF[kind]
        self._vu[key] = round(level, 2)
        now = time.time()
        if now - self._vu_sent >= 0.15:
            self._vu_sent = now
            self.hub.publish("vu", dict(self._vu))
        if key != "mic":
            return          # 電腦沒在播聲音是正常的，只有麥克風完全沒聲音才需要提醒
        quiet = self.sources["mic"]["quiet"]
        if level >= MIC_HEARD_LEVEL:
            self._mic_heard = True
        # 只有「從開始就一直完全沒聲音」才提醒（選錯麥克風、被靜音、被 Windows 擋住）。
        # 有些麥克風（USB 麥克風、有降噪的）沒人說話的時候送來的是一串 0：說過話之後的安靜是正常的，不能每停六秒就跳出「麥克風完全沒有聲音」
        if silent_for >= 6 and not quiet and not self._mic_heard:
            blocked = oswin.mic_privacy_blocked()
            self.log(f"mic is silent for {silent_for:.0f}s on {self.sources['mic']['device']!r}, privacy blocked: {blocked}")
            self._source("mic", quiet="blocked" if blocked else "silent")
        elif silent_for < 1 and quiet:
            self._source("mic", quiet="")

    def _restart_voice(self, key: str) -> None:
        self._stop_voice(key)
        threading.Thread(target=self._start_voice, args=(key,), daemon=True).start()

    # ---- 語音辨識用顯示卡（實驗性：要有 NVIDIA 顯示卡、顯示卡版的翻譯引擎，再另外下載 cuDNN）

    def speech_gpu_state(self) -> dict:
        return dict(self.speech_gpu, can=self.gpu.get("tier", "cpu") != "cpu")

    def _speech_gpu(self, **changes) -> None:
        self.speech_gpu.update(changes)
        self.hub.publish("speech_gpu", self.speech_gpu_state())

    def _speech_gpu_files(self) -> list | None:
        """用顯示卡辨識需要的兩個資料夾（CUDA、cuDNN）；有缺就回傳 None。"""
        runtime = assets.cuda_runtime_dir(self.store)
        return [runtime, assets.cudnn_dir(self.store)] if runtime and assets.has_cudnn(self.store) else None

    def _speech_engine(self) -> tuple[str, str]:
        """語音辨識現在該用什麼跑：（"cuda", "float16"）或（"cpu", "int8"）。順便更新給介面看的狀態。"""
        if self.cfg["speech_device"] != "gpu" or self.gpu.get("tier", "cpu") == "cpu":
            self._speech_gpu(state="off", active=False, error="")
            return "cpu", "int8"
        if self.speech_gpu["state"] in ("failed", "downloading"):
            return "cpu", "int8"
        folders = self._speech_gpu_files()
        if not folders:
            if assets.has_cudnn(self.store):      # cuDNN 有了，缺的是顯示卡版翻譯引擎帶來的 CUDA 檔案：下載也沒用
                self._speech_gpu(state="failed", active=False,
                                 error="the graphics card version of the translation engine is not installed")
            else:
                self._speech_gpu(state="need", active=False)
            return "cpu", "int8"
        oswin.add_dll_dirs(folders)
        self._speech_gpu(state="ready", active=True, error="")
        return "cuda", "float16"

    def _speech_gpu_failed(self, ex: Exception) -> None:
        self.log(f"speech recognition on the graphics card failed, using the processor instead: {ex!r}")
        self._speech_gpu(state="failed", active=False, error=str(ex)[:300])
        self.notice("speech_gpu_failed")

    def _speech_device_changed(self) -> None:
        """換了「用處理器／用顯示卡」或檔案下載好了：辨識器重新載入，收音中的來源重開。"""
        if self.speech_gpu["state"] == "failed":
            self.speech_gpu["state"] = "off"      # 再選一次顯示卡 = 再試一次
        with self._lock:
            self.recognizer = None
        self._speech_engine()
        for key in ("listen", "mic"):
            if self.sources[key]["on"]:
                self._restart_voice(key)

    def speech_gpu_download(self, cancel: bool = False) -> bool:
        """下載用顯示卡辨識需要的 cuDNN（約 700 MB）。"""
        if cancel:
            self._speech_gpu_cancel.set()
            return True
        if self.speech_gpu["state"] == "downloading" or self.gpu.get("tier", "cpu") == "cpu":
            return False
        self._speech_gpu_cancel.clear()
        self._speech_gpu(state="downloading", percent=0, error="")

        def work():
            try:
                assets.get_cudnn(self.store, lambda done, total: self._speech_gpu(
                    percent=int(done * 100 / total) if total else 0), self._speech_gpu_cancel.is_set)
            except Exception as ex:  # noqa: BLE001
                self.log(f"cuDNN download failed: {ex!r}")
                cancelled = self._speech_gpu_cancel.is_set()
                self._speech_gpu(state="need", percent=0, error="" if cancelled else str(ex)[:300])
                return
            self.speech_gpu["state"] = "off"
            self._speech_device_changed()

        threading.Thread(target=work, daemon=True, name="cudnn").start()
        return True

    # ---- 分辨說話者

    def speakers_state(self) -> dict:
        """給介面：有沒有開、模型的狀態、目前分出來哪幾個人（編號、取的名字、說了幾句）。"""
        people = self.voices.speakers.people() if self.voices else []
        remember = bool(self.cfg["remember_speakers"])
        return {**self.speakers, "on": bool(self.cfg["tell_speakers"]), "run": self._speaker_run, "remember": remember,
                # known：這個人會被記住（有開「記住」而且取了名字）
                # lang、lang2：使用者幫這個人設的主語言、副語言（沒設是空的）
                "people": [dict(p, name=self._speaker_names.get(p["id"], ""), known=remember and bool(self._speaker_names.get(p["id"])),
                                lang=self._speaker_langs.get(p["id"], ("", ""))[0], lang2=self._speaker_langs.get(p["id"], ("", ""))[1])
                           for p in people]}

    def _speakers(self, **changes) -> None:
        self.speakers.update(changes)
        self.hub.publish("speakers", self.speakers_state())

    def _voices_now(self):
        """語音那邊每一句說完會來問：現在要不要分辨是誰（要的話回傳分辨用的東西）。"""
        return self.voices if self.cfg["tell_speakers"] and self.speakers["state"] == "ready" else None

    def speakers_load(self, retry: bool = False) -> None:
        """開了「分辨說話者」：模型沒有就先下載（約 27 MB），再載入。都在背景做，好了之後下一句開始就會標是誰。"""
        if not self.cfg["tell_speakers"]:
            self._speakers(state="off", error="")
            return
        if self.voices:
            self._speakers(state="ready", error="")
            return
        if self.speakers["state"] in ("downloading", "loading") or (self.speakers["state"] == "failed" and not retry):
            return
        self._speakers(state="downloading" if not assets.has_voice(self.store) else "loading", percent=0, error="")

        def work():
            try:
                path = assets.get_voice(self.store, lambda done, total: self._speakers(
                    percent=int(done * 100 / total) if total else 0), self.closing.is_set)
                self._speakers(state="loading")
                from .core.voices import Voices

                self.voices = Voices(path)
                self._voices_seed()
            except Exception as ex:  # noqa: BLE001
                self.log(f"speaker model not ready: {ex!r}")
                self._speakers(state="failed", error=str(ex)[:300])
                return
            self.log("speaker model loaded")
            self._speakers(state="ready" if self.cfg["tell_speakers"] else "off", error="")
            self._storage_changed()

        threading.Thread(target=work, daemon=True, name="voices").start()

    def speaker_rename(self, speaker: int, name: str) -> dict:
        """幫第幾個說話者取名字（空的 = 改回「說話者 n」）。只記在這一次開啟期間。"""
        name = " ".join(str(name or "").split())[:24]
        if not self.voices or not any(p["id"] == speaker for p in self.voices.speakers.people()):
            return {"ok": False}
        if name:
            self._speaker_names[speaker] = name
        else:
            self._speaker_names.pop(speaker, None)
        with self._lock:      # 畫面上已經有的句子也跟著換名字
            for i, old in enumerate(self.messages):
                if old.get("speaker") == speaker and old.get("speaker_run") == self._speaker_run:
                    self.messages[i] = dict(old, voice=name)
        self.transcript.name_speaker(speaker, name)
        self._voices_save()      # 有開「記住」：取了名字就記住，名字清空就忘掉
        self.hub.publish("speakers", self.speakers_state())
        return {"ok": True, "name": name}

    def speaker_language(self, speaker: int, lang: str, lang2: str = "") -> dict:
        """
        幫第幾個說話者設他說的語言。lang：主語言（平常說的）；lang2：副語言（偶爾夾著說的，可以沒有）。
        都是語言代碼（跟「對方的語言」同一份清單）；lang 給空的 = 不指定，照「對方的語言」聽。
        設了之後，他說的話只在這兩個語言裡判斷，主語言優先；翻譯一樣翻成「我的語言」。
        沒取名字的人只記到程式關掉；取了名字而且有開「記住取過名字的人」，語言跟名字一起記到下次。
        """
        lang, lang2 = self._clean_speaker_langs(lang, lang2)
        if not self.voices or not any(p["id"] == speaker for p in self.voices.speakers.people()):
            return {"ok": False}
        if lang:
            self._speaker_langs[speaker] = (lang, lang2)
        else:
            self._speaker_langs.pop(speaker, None)
        self._voices_save()
        self.hub.publish("speakers", self.speakers_state())
        return {"ok": True, "lang": lang, "lang2": lang2}

    @staticmethod
    def _clean_speaker_langs(lang, lang2) -> tuple[str, str]:
        """只收語音辨識聽得懂的語言；沒有主語言就沒有副語言；副語言跟主語言是同一種話（例如繁體和簡體）就不算。"""
        def ok(code) -> str:
            code = str(code or "")
            return code if code in LANGS and LANGS[code]["family"] in VOICE_FAMILIES else ""

        lang, lang2 = ok(lang), ok(lang2)
        if not lang or (lang2 and LANGS[lang2]["family"] == LANGS[lang]["family"]):
            lang2 = ""
        return lang, lang2

    def _speaker_lang_table(self) -> dict[int, list[str]]:
        """給收音管線：{說話者編號: [主語言, 副語言]}（語音辨識用的語言名，中文不分繁簡）。只有設過語言的人。"""
        return {n: [LANGS[code]["family"] for code in pair if code] for n, pair in list(self._speaker_langs.items())}

    def speakers_forget(self) -> dict:
        """全部重新分辨：忘掉目前分出來的人和名字（換了一群人，或分錯了）。之前的句子不再標是誰。
        以前記住的人（存在 voices.json 的）不會被忘掉：照存檔的樣子放回來，要忘掉某個人是把他的名字清空。"""
        if self.voices:
            self.voices.speakers.forget()
        self._speaker_names.clear()
        self._speaker_langs.clear()
        self._speaker_run += 1
        self.transcript.forget_speakers()
        self._voices_seed()
        self.hub.publish("speakers", self.speakers_state())
        return {"ok": True}

    def _voices_seed(self) -> None:
        """開始分人之前：有開「記住取過名字的人」就把記住的人先放進去，他們一開口就認得、直接顯示名字。"""
        if not (self.voices and self.cfg["remember_speakers"]):
            return
        for person in voicebook.load(self.data):
            n = self.voices.speakers.seed(person["voice"], person["weight"])
            self._speaker_names[n] = person["name"]
            lang, lang2 = self._clean_speaker_langs(person.get("lang"), person.get("lang2"))
            if lang:
                self._speaker_langs[n] = (lang, lang2)

    def _voices_save(self) -> None:
        """把現在取過名字的人存起來（整份換掉）。沒開「記住」、或聲紋模型還沒準備好（還不知道以前記了誰）就不動那個檔。"""
        if not (self.voices and self.cfg["remember_speakers"]):
            return
        people = []
        for n, name in sorted(self._speaker_names.items()):
            got = self.voices.speakers.voice_of(n)
            if got and name:
                lang, lang2 = self._speaker_langs.get(n, ("", ""))
                people.append({"name": name, "voice": got[0], "weight": got[1], "lang": lang, "lang2": lang2})
        self._voices_saved = time.time()
        if not voicebook.save(self.data, people):
            self.log("remembered voices could not be saved")

    def _remember_changed(self) -> None:
        """「記住取過名字的人」開或關：開就把現在取過名字的人存起來，關就把存的刪掉（這一次開啟期間的名字還在）。"""
        if self.cfg["remember_speakers"]:
            self._voices_save()
        else:
            voicebook.clear(self.data)
        self.hub.publish("speakers", self.speakers_state())

    def speaker_tag(self, msg: dict) -> tuple | None:
        """這一則要標成誰：（第幾個說話者, 取的名字）。沒分出來、或是「重新分辨」之前標的，就沒有。"""
        n = msg.get("speaker")
        if not n or msg.get("speaker_run") != self._speaker_run or not self.cfg["tell_speakers"]:
            return None
        return int(n), self._speaker_names.get(int(n), "")

    # ---- 只聽指定程式的聲音

    def apps_state(self) -> dict:
        """給設定頁的清單：現在開著哪些程式、選了哪些（選了但現在沒開的也列出來）。"""
        chosen = list(self.cfg["listen_apps"])
        try:
            rows = apps.running()
        except Exception as ex:  # noqa: BLE001
            self.log(f"listing programs failed: {ex!r}")
            rows = []
        seen = {r["exe"].lower() for r in rows}
        low = {c.lower() for c in chosen}
        out = [{"exe": r["exe"], "name": r["name"], "title": r["title"], "running": True, "chosen": r["exe"].lower() in low}
               for r in rows]
        out += [{"exe": c, "name": apps.display_name(c), "title": "", "running": False, "chosen": True}
                for c in chosen if c.lower() not in seen]
        available = bool(getattr(self.audio, "apps_available", lambda: False)())
        return {"ok": True, "available": available, "off": self._apps_audio_off, "apps": out, "chosen": chosen}

    def _apps_audio_error(self, kind: str, ex: Exception) -> None:
        """指定的程式有開，但 Windows 不給它的聲音：這次先改聽整台電腦，並告訴使用者。"""
        self.log(f"listening to chosen programs only failed, listening to the whole computer instead: {ex!r}")
        self._apps_audio_off = True
        self.notice("apps_audio_failed")
        if self.sources["listen"]["on"] or self.sources["listen"]["busy"]:
            self._restart_voice("listen")

    def _mic_gate(self) -> bool:
        """麥克風現在要不要收音：開了「按住才說話」就只有按著那個鍵的時候才收。"""
        return not self.cfg["ptt"] or oswin.key_down(self.cfg["ptt_key"])

    def _is_echo(self, kind: str, text: str, lang: str) -> bool:
        """
        麥克風收到喇叭播出來的對方聲音（沒戴耳機的時候）：同一句話會從兩邊各進來一次。
        幾秒內兩邊聽到幾乎一樣的句子，就只留「對方」那一句。回傳 True 表示這句（麥克風的）不要了。
        """
        now = time.time()
        other = "mic" if kind == "loopback" else "loopback"
        twin = next((r for r in self._recent_voice if r["kind"] == other and now - r["at"] <= 6 and r["lang"] == lang
                     and similarity(r["text"], text) >= 0.6), None)
        if kind == "mic":
            if twin:
                self.log("mic sentence dropped: it is the other side's voice coming out of the speakers")
            return bool(twin)
        if twin and twin.get("id"):      # 麥克風那句先到了：把它收回來
            self.log("mic sentence removed: the same sentence just came from the other side")
            self._drop(twin["id"])
            self._recent_voice.remove(twin)
        return False

    def _on_speech(self, kind: str, text: str, lang: str, seconds: float = 0.0, unsure: bool = False,
                   speaker: int | None = None, length: float = 0.0, loud: float = 0.0) -> None:
        """seconds：辨識花了幾秒；length：這句話本身說了幾秒；loud：比這條收音平常大聲幾倍。"""
        if lang == "error":
            self._source(SOURCE_OF[kind], error=text)
            self._end_live(kind)
            return
        if self.cfg["drop_echo"] and self._is_echo(kind, text, lang):
            self._end_live(kind)
            return
        if self._is_own_voice(text):
            self.log(f"{kind} sentence dropped: it is LiveLingo reading my translation aloud")
            self._end_live(kind)
            return
        if kind == "mic" and self.cfg["voice_commands"]:
            command = voicecmd.match(text, self.cfg["ui_lang"], self.cfg["my_lang"], lang)      # 整句話就是語音指令（2.18.0）：照做，不翻
            if command:
                self._end_live(kind)
                threading.Thread(target=self._voice_command, args=(command,), daemon=True, name="voicecmd").start()
                return
        if self.voice_paused:      # 說了「暫停翻譯」
            self._end_live(kind)
            return
        if kind == "loopback":
            self._listen_extras(text, lang, unsure, speaker, length)
        if self.cfg["drop_fillers"] and textfix.only_filler(text):      # 只有「嗯」「啊」、笑聲（2.15.0）
            self._end_live(kind)
            return
        if self.cfg["join_split"] and self._join_split(kind, text, lang, seconds, unsure, speaker, length):
            return
        own = self._speaker_langs.get(int(speaker)) if speaker and kind == "loopback" else None
        if kind == "loopback" and self.cfg["their_auto"] and not own:
            # 設過語言的人說的話不拿來猜「對方的語言」：他的語言是使用者指定的，不代表其他人也換了語言
            self._their_language_heard(lang, text)
        text = self._voice_script(kind, text, lang, own)
        heard = text
        msg_id = self.submit("heard" if kind == "loopback" else "spoken", text, lang, unsure=unsure, hear=seconds,
                             live=self._take_live(kind), speaker=speaker if kind == "loopback" else None, **({"loud": loud} if loud else {}))
        if msg_id and length:
            self._msg_len[msg_id] = float(length)
        if speaker:
            self.hub.publish("speakers", self.speakers_state())      # 多了一個人，或某個人又多說一句
            if self._speaker_names.get(int(speaker)) and time.time() - self._voices_saved > VOICES_SAVE_EVERY:
                self._voices_save()      # 記住的人：聲紋跟著今天的聲音調整過了，隔一陣子存一次
        self._recent_voice.append({"kind": kind, "text": heard, "lang": lang, "at": time.time(),
                                   "end": time.time() - float(seconds or 0.0), "id": msg_id})

    def _join_split(self, kind: str, text: str, lang: str, seconds: float, unsure: bool, speaker, length: float = 0.0) -> bool:
        """斷句修補（2.15.0）：上一句同一個人、同一個語言，沒說完（沒有句號、很短），這一句緊接著就開始說：
        接回上一句，整句重翻，不另外開一則。上一句還在翻的話不接（不能動正在翻的那一則）。"""
        with self._lock:
            last = next((v for v in reversed(self._recent_voice) if v["kind"] == kind), None)
            msg = next((m for m in self.messages if last and m["id"] == last.get("id")), None) if last else None
        if not last or not msg or msg.get("pending") or msg.get("error") or last["lang"] != lang:
            return False
        if (msg.get("speaker") or None) != (speaker if kind == "loopback" else None) and kind == "loopback":
            return False
        # 這句開始說的時間 = 現在 − 辨識花的時間 − 這句話的長度；上一句結束 ≈ 上一句辨識好的時間 − 它辨識花的時間
        gap = max(0.0, time.time() - float(seconds or 0.0) - float(length or 0.0) - float(last.get("end") or last["at"]))
        if not textfix.should_join(msg["original"], text, gap, family_of(lang) or lang):
            return False
        joined = textfix.join(msg["original"], self._voice_script(kind, text, lang, None), family_of(lang) or lang)
        self._end_live(kind)
        self.log(f"{kind}: joined a sentence that was cut in two ({gap:.1f} s apart)")
        msg = dict(msg, original=joined, pending=True, copied=False, back="", error="", why="", joined=True,
                   unsure=bool(msg.get("unsure") or unsure), hear=float(msg.get("hear") or 0.0) + float(seconds or 0.0))
        self._post(msg)
        self.jobs.put(msg, asked=True)
        last.update(text=joined, at=time.time(), end=time.time() - float(seconds or 0.0))
        return True

    def _is_own_voice(self, text: str) -> bool:
        """收到的是 LiveLingo 自己念出來的譯文（念到喇叭、被「聽對方」或麥克風收到）：不要再翻一次。
        念給我聽的對方的話（2.14.0）也算：沒戴耳機時麥克風會收到，不能被當成我說的話翻出去。"""
        said = self.speaker.recent() + self.reader.recent()
        return bool(said) and any(similarity(t, text) >= 0.6 for t in said)

    # ------------------------------------------------------------------ 語言學習：生字和單字本（2.10.0）

    def _learn(self, msg: dict) -> None:
        """學習模式：對方的一句話翻好之後，在背景挑一兩個生字、查意思，送 words 事件給介面標在字幕旁邊。
        翻譯還有在排隊的時候就不查（不能讓生字拖慢翻譯）。"""
        if not self.cfg["learn_mode"] or msg.get("who") == "me" or msg.get("via") == "typed":
            return
        lang = family_of(msg.get("lang") or "")
        if not lang or lang == family_of(self.cfg["my_lang"]):
            return
        self.wordbook.seen_again(msg.get("original", ""), lang)      # 學習統計：又遇到單字本裡的字（2.19.0）
        level = self.cfg["learn_level"] if self.cfg["learn_level"] in LEARN_LEVELS else "middle"
        words = self.wordbook.fresh(msg.get("original", ""), lang, level=level)
        if not words:
            return

        def work():
            items = []
            for w in words:
                if self.jobs.waiting() or self.closing.is_set():
                    break
                meaning = self._gloss(w, lang, msg.get("original", ""))
                if meaning:
                    item = {"w": w, "lang": lang, "meaning": meaning}
                    if self._hanja_on(lang):
                        item["hanja"] = study.hanja_of(w)
                    items.append(item)
            if items:
                with self._lock:
                    self._words[msg["id"]] = items
                    for old in sorted(self._words)[:-MAX_MARKED]:
                        self._words.pop(old, None)
                self.hub.publish("words", {"id": msg["id"], "items": items})

        threading.Thread(target=work, daemon=True, name="learn").start()

    def _gloss(self, word: str, lang: str, sentence: str = "") -> str:
        """一個字的意思（我的語言）。詞彙表或用語包裡有的直接用（遊戲用語模型常翻錯）；沒有的才帶著整句問模型。翻不出來回傳空字串。"""
        from .core import assist

        mine = self.cfg["my_lang"]
        key = word.lower()
        for src, dst in self.glossary.as_dict(self._game_words()).items():
            if src.lower() == key:
                return dst
        if lang == "ko" and mine in ("zh-TW", "zh-CN"):
            if self._all_terms is None:
                self._all_terms = TermPack()      # 所有的用語包：查字的意思不用管現在在玩哪個遊戲
            for term, dst in self._all_terms.find(word, "ko", mine):
                if term == word:
                    return dst
        try:
            self._wake_engine()
            return assist.parse_gloss(self.translator.ask_free(assist.gloss_prompt(word, sentence, mine), 40), word, mine)
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())
            return ""

    def word_action(self, action: str, word: str, lang: str, meaning: str = "", msg_id=None, remembered: bool = False) -> dict:
        """單字本：add（收進來；沒給意思就查）/ remove / know（我會了，以後不標）/ review（複習完，remembered 記得嗎）/ meaning（改意思）。"""
        word, lang = str(word or "").strip(), family_of(str(lang or ""))
        if not word or not lang:
            return {"ok": False, "error": "nothing"}
        if action == "add":
            example, example_tr = "", ""
            with self._lock:
                msg = next((m for m in self.messages if m["id"] == msg_id), None) if msg_id is not None else None
            if msg:
                example, example_tr = msg.get("original", ""), msg.get("translated", "")
            if not meaning:
                if self.phase != "ready":
                    return {"ok": False, "error": "not_ready"}
                row = self.wordbook.add(word, lang, "", example, example_tr)

                def work():
                    found = self._gloss(word, lang, example)
                    if found:
                        self.wordbook.add(word, lang, found, hanja=self._hanja(word, lang))
                    self.hub.publish("wordbook", self.wordbook.state())

                threading.Thread(target=work, daemon=True, name="gloss").start()
            else:
                row = self.wordbook.add(word, lang, meaning, example, example_tr, hanja=study.hanja_of(word) if self._hanja_on(lang) else "")
            if row is None:
                return {"ok": False, "error": "nothing"}
        elif action == "remove":
            self.wordbook.remove(word, lang)
        elif action == "know":
            self.wordbook.know(word, lang)
            with self._lock:      # 已經標在字幕旁邊的也拿掉
                for mid, items in list(self._words.items()):
                    keep = [x for x in items if not (x["lang"] == lang and x["w"].lower() == word.lower())]
                    if len(keep) != len(items):
                        self._words[mid] = keep
                        self.hub.publish("words", {"id": mid, "items": keep})
        elif action == "review":
            if self.wordbook.review(word, lang, bool(remembered)) is None:
                return {"ok": False, "error": "nothing"}
        elif action == "say":      # 單字發音（2.19.0）：用 Windows 的語音念出來
            return {"ok": bool(self.reader.say(word, lang))}
        elif action == "meaning":
            if self.wordbook.add(word, lang, str(meaning or "")[:200]) is None:
                return {"ok": False, "error": "nothing"}
        else:
            return {"ok": False, "error": "unknown"}
        state = self.wordbook.state()
        self.hub.publish("wordbook", state)
        return {"ok": True, **state}

    def words_list(self, q: str = "", review: bool = False) -> dict:
        return {"items": self.wordbook.due() if review else self.wordbook.listing(q), **self.wordbook.state()}

    # ------------------------------------------------------------------ 學習工具（2.19.0）

    def _hanja_on(self, lang: str) -> bool:
        return lang == "ko" and bool(self.cfg["hanja"]) and family_of(self.cfg["my_lang"]) in ("zh", "ja")

    def _hanja(self, word: str, lang: str) -> str:
        """韓文字的漢字：表裡有就用表的；沒有才問模型，答案要每個字都對得上才收。"""
        if not self._hanja_on(lang):
            return ""
        found = study.hanja_of(word)
        if found or self.phase != "ready":
            return found
        try:
            answer = self.translator.ask_free(study.hanja_prompt(word), 16).strip().splitlines()
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())
            return ""
        answer = answer[0].strip().strip("\"'「」") if answer else ""
        return answer if study.hanja_ok(word, answer) else ""

    def learn_data(self) -> dict:
        """學習頁的資料：統計、收藏的句子。"""
        with self.wordbook._lock:
            sentences = [dict(x) for x in reversed(self.wordbook.sentences)]
        return {**self.wordbook.state(), "stats": self.wordbook.stats(), "sentences": sentences}

    def study_quiz(self, n: int = 10) -> dict:
        """用自己的單字本出題（59）。"""
        items = study.quiz(self.wordbook.listing(), max(1, min(20, int(n or 10))))
        return {"ok": bool(items), "items": items, "error": "" if items else "few"}

    def sentence_action(self, action: str, msg_id=None, note: str = "", sid: str = "", text: str = "", lang: str = "") -> dict:
        """收藏整句（61）：save（從一句字幕收，或直接給 text）/ note（改說明）/ remove。"""
        if action == "remove":
            ok = self.wordbook.remove_sentence(str(sid))
        elif action in ("save", "note"):
            tr = ""
            if msg_id is not None:
                with self._lock:
                    msg = next((m for m in self.messages if m["id"] == msg_id), None)
                if not msg:
                    return {"ok": False, "error": "nothing"}
                text, lang, tr = msg.get("original", ""), msg.get("lang", ""), msg.get("translated", "")
                if not note:
                    note = self._explained.get(msg_id, "").split("\n")[0]
            ok = self.wordbook.add_sentence(text, lang, tr, note) is not None
        else:
            return {"ok": False, "error": "unknown"}
        self.hub.publish("wordbook", self.wordbook.state())
        return {"ok": bool(ok), **self.learn_data()}

    def explain(self, msg_id) -> dict:
        """問「為什麼這樣說」（66）：在背景問翻譯模型，好了送 explain 事件。"""
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if not msg or not msg.get("original"):
            return {"ok": False, "error": "nothing"}
        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        if msg_id in self._explained:
            self.hub.publish("explain", {"id": msg_id, "text": self._explained[msg_id]})
            return {"ok": True}

        def work():
            text = ""
            try:
                self._wake_engine()
                text = study.parse_explain(self.translator.ask_free(
                    study.explain_prompt(msg["original"], msg.get("translated", ""), self.cfg["my_lang"]), 160))
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            if text:
                self._explained[msg_id] = text
                for old in list(self._explained)[:-MAX_MARKED]:
                    self._explained.pop(old, None)
            self.hub.publish("explain", {"id": msg_id, "text": text})

        threading.Thread(target=work, daemon=True, name="explain").start()
        return {"ok": True}

    def shadow(self, audio: str, text: str, lang: str) -> dict:
        """跟讀打分（60）：介面錄好的聲音（data URL）→ 語音辨識 → 跟原句比。"""
        import base64
        import tempfile

        text, lang = str(text or "").strip(), str(lang or "")
        raw = str(audio or "")
        if not text or not raw:
            return {"ok": False, "error": "nothing"}
        try:
            data = base64.b64decode(raw.split(",", 1)[-1])
        except ValueError:
            return {"ok": False, "error": "audio"}
        if not data or len(data) > 8_000_000:
            return {"ok": False, "error": "audio"}
        path = Path(tempfile.gettempdir()) / f"livelingo-shadow-{os.getpid()}.webm"
        try:
            path.write_bytes(data)
            sound = filesubs.decode(str(path))
            heard = self._ensure_recognizer().transcribe(sound, [family_of(lang) or "en"]) if len(sound) else None
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())
            return {"ok": False, "error": "audio"}
        finally:
            path.unlink(missing_ok=True)
        said = heard[0] if heard else ""
        return {"ok": True, "heard": said, **study.shadow_score(text, said, lang)}

    def anki_export(self) -> dict:
        """匯出到 Anki（64）：存成 Anki「匯入檔案」看得懂的文字檔，打開資料夾。"""
        words = self.wordbook.listing()
        with self.wordbook._lock:
            sentences = [dict(x) for x in self.wordbook.sentences]
        if not words and not sentences:
            return {"ok": False, "error": "empty"}
        try:
            path = study.save_anki(self.transcript_dir(), words, sentences)
        except OSError as ex:
            self.log(f"anki export failed: {ex!r}")
            return {"ok": False, "error": "save"}
        oswin.open_path(path.parent)
        return {"ok": True, "path": str(path), "name": path.name, "count": len(words) + len(sentences)}

    def practice_list(self) -> dict:
        """對話回放練習（68）：歷史裡對方說的話，一段一段。"""
        if not self.history.keep:
            return {"ok": False, "error": "history_off", "items": []}
        rows = self.history._matching()
        items = study.practice_sessions(rows, RECAP_GAP, family_of(self.cfg["my_lang"]))
        return {"ok": True, "items": items, "error": "" if items else "empty"}

    def practice_say(self, text: str, lang: str) -> dict:
        """念一句給我聽（用 Windows 的語音）。"""
        text = str(text or "").strip()[:300]
        return {"ok": bool(text) and bool(self.reader.say(text, str(lang or "")))}

    def _review_remind(self) -> None:
        """每日複習提醒（57）：一天一次，有該複習的字就在工具列跳通知，主視窗也跳一則。"""
        while not self.closing.wait(REMIND_EVERY):
            try:
                self._review_remind_once()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

    def _review_remind_once(self) -> bool:
        if not self.cfg["review_remind"]:
            return False
        today = time.strftime("%Y-%m-%d")
        due = self.wordbook.state()["due"]
        if not due or self.cfg["review_reminded"] == today:
            return False
        self.cfg.set(review_reminded=today)
        self.tray.notify(study.remind_text(self.cfg["ui_lang"], due))
        self.notice("review_due", count=due)
        return True

    # ------------------------------------------------------------------ 對話摘要（2.10.0）

    def _recap_rows(self) -> list[dict]:
        """還沒摘要過的句子，照時間排。有存歷史就用歷史（重開程式也接得上），沒存就用畫面上的。"""
        since = self.recaps.until
        if self.history.keep:
            rows = self.history.search(limit=200)["items"][::-1]      # 最新的 200 句就夠了（摘要也只看最後 80 句）
        else:
            with self._lock:
                rows = [m for m in self.messages if not m.get("pending") and not m.get("error") and m.get("translated")]
        return [r for r in rows if float(r.get("at") or 0) > since]

    def recap_check(self) -> None:
        """自動摘要：最後一段對話安靜超過 10 分鐘、夠長、還沒摘要過，就整理一份。"""
        if not self.cfg["recap_auto"] or self.phase != "ready" or self._recap.get("state") == "thinking":
            return
        now = time.time()
        for part in sessions(self._recap_rows()):      # 舊的先做，一次做一段；太久以前的（例如剛更新完，歷史裡一個月的對話）不補做
            end = float(part[-1].get("at") or 0)
            if now - RECAP_RECENT < end <= now - RECAP_GAP and worth(part):
                self._summarize(part)
                return

    def recap_now(self) -> dict:
        """「現在摘要」：最近這一段（還沒摘要過的）馬上整理。"""
        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        if self._recap.get("state") == "thinking":
            return {"ok": False, "error": "busy"}
        parts = sessions(self._recap_rows())
        if not parts or not worth(parts[-1], 2):
            return {"ok": False, "error": "nothing"}
        self._summarize(parts[-1])
        return {"ok": True}

    def _summarize(self, lines: list[dict]) -> None:
        from .core import assist

        self._recap = {"state": "thinking", "id": ""}
        self.hub.publish("recaps", dict(self.recaps.state(), **self._recap))

        def work():
            row = None
            try:
                self._wake_engine()
                names = chatpaint.NAMES.get(self.cfg["my_lang"]) or chatpaint.NAMES["en"]
                out = self.translator.ask_free(assist.summary_prompt(lines, self.cfg["my_lang"], {"me": names["me"], "them": names["heard"]}), 400)
                row = self.recaps.add(lines, assist.parse_summary(out, self.cfg["my_lang"]), self.cfg["my_lang"])
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            self._recap = {"state": "ready" if row else "failed", "id": row["id"] if row else ""}
            self.hub.publish("recaps", dict(self.recaps.state(), **self._recap))
            if row:
                self.notice("recap_ready", n=len(row["points"]))

        threading.Thread(target=work, daemon=True, name="recap").start()

    def recap_remove(self, rid: str) -> dict:
        self.recaps.remove(rid)
        self._recap = {"state": "off", "id": ""}
        self.hub.publish("recaps", dict(self.recaps.state(), **self._recap))
        return {"ok": True}

    # ------------------------------------------------------------------ 2.14.0：不翻的字、名字建議、隱私暫停、開遊戲就開始、念給我聽、情境

    def _words_now(self) -> dict[str, str]:
        """翻譯時要照用的詞：詞彙表（通用的加上現在這個遊戲的），再加上不翻的字（原樣 = 原樣）。詞彙表裡寫了譯法的優先。"""
        out = self.glossary.as_dict(self._game_words())
        have = {k.lower() for k in out}
        for word in self.cfg["keep_words"]:
            if word.lower() not in have:
                out[word] = word
        return out

    # ------------------------------------------------------------------ 2.17.0：圖片、手把、快捷鍵一覽、釘選、送進遊戲聊天

    def image_translate(self, data_url: str) -> dict:
        """拖進主視窗的圖片（介面讀成 data URL 送來）：讀字、一段一段翻，結果出現在對話裡。"""
        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        bgr = imagetext.from_data_url(data_url)
        if bgr is None:
            return {"ok": False, "error": "bad_image"}
        n = self._read_image(bgr)
        return {"ok": True, "n": n} if n else {"ok": False, "error": "no_text"}

    def clipboard_translate(self) -> dict:
        """剪貼簿裡的圖（Win+Shift+S 截完按快速鍵）：讀字翻譯，結果在滑鼠旁邊的小卡上。"""
        if self.phase != "ready":
            self._note("not_ready")
            return {"ok": False, "error": "not_ready"}
        bgr = imagetext.clipboard_image()
        if bgr is None:
            self._note("clip_empty")
            return {"ok": False, "error": "clip_empty"}
        n = self._read_image(bgr, card=True)
        return {"ok": True, "n": n} if n else {"ok": False, "error": "no_text"}

    def camera_translate(self) -> dict:
        """網路攝影機拍一張（菜單、說明書、包裝），讀字翻譯。拍完攝影機馬上關掉；照片只有開著相簿時才留。"""
        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        bgr = imagetext.camera_frame(int(self.cfg["camera_index"]))
        if bgr is None:
            self._note("camera_failed")
            return {"ok": False, "error": "camera_failed"}
        n = self._read_image(bgr)
        return {"ok": True, "n": n} if n else {"ok": False, "error": "no_text"}

    def _apply_gamepad(self) -> None:
        self.gamepad.set(gamepad_mod.clean(self.cfg["gamepad_keys"], HOTKEY_ACTIONS))
        if self.cfg["gamepad_on"]:
            if not self.gamepad.start():
                self.log("gamepad: XInput is not available on this computer")
        else:
            self.gamepad.stop()
        self.hub.publish("gamepad", {"available": self.gamepad.available, "on": bool(self.cfg["gamepad_on"])})

    def _pad_action(self, name: str) -> None:
        """手把組合按下去：跟按鍵盤快速鍵一樣。"""
        if name in HOTKEY_ACTIONS:
            self._hotkey(name)

    def show_keys(self) -> list[str]:
        """快捷鍵一覽：浮動字幕上跳一張小卡（停 KEYS_SECONDS 秒），主視窗也顯示一份。"""
        pads = gamepad_mod.clean(self.cfg["gamepad_keys"], HOTKEY_ACTIONS) if self.cfg["gamepad_on"] else {}
        lines = cheatsheet.lines(dict(self.cfg["hotkeys"]), pads, self.cfg["ui_lang"])
        if not lines:
            self._note("keys_none")
            return []
        self.overlay.on_removed("note")
        self.overlay.push("note", "  ·  ".join(lines), key="note", life=KEYS_SECONDS)
        self.hub.publish("keys_card", {"lines": lines})
        return lines

    def pin_action(self, mid, on: bool = True) -> dict:
        """把一句釘在浮動字幕最上面（不會被新的洗掉），最多 MAX_PINS 句；再釘一句會把最早釘的拿掉。"""
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            return {"ok": False, "error": "unknown"}
        changed = []
        with self._lock:
            have = {m["id"]: m for m in self.messages}
            msg = have.get(mid)
            if msg is None or (on and (msg.get("pending") or msg.get("error") or not msg.get("translated"))):
                return {"ok": False, "error": "unknown"}
            self.pins = [p for p in self.pins if p in have and p != mid]
            if on:
                self.pins.append(mid)
                for old in self.pins[:-MAX_PINS]:
                    have[old].pop("pinned", None)
                    changed.append(dict(have[old]))
                self.pins = self.pins[-MAX_PINS:]
                msg["pinned"] = True
            else:
                msg.pop("pinned", None)
            changed.append(dict(msg))
            pinned = [dict(have[p]) for p in self.pins]
        for m in changed:
            self.hub.publish("message", m)
        self.overlay.set_pins(pinned)
        return {"ok": True, "pins": list(self.pins)}

    def _unpin_gone(self, msg_id: int) -> None:
        """釘選的句子被拿掉了（對話裡刪了）：浮動字幕上也拿掉。"""
        with self._lock:
            self.pins = [p for p in self.pins if p != msg_id]
            have = {m["id"]: m for m in self.messages}
            pinned = [dict(have[p]) for p in self.pins if p in have]
        self.overlay.set_pins(pinned)

    def _chat_send(self, msg: dict, out: str) -> None:
        """說話直接送進遊戲聊天（預設關）：我用麥克風說的話翻好後，替我按開聊天框的鍵、貼上、按 Enter。
        LiveLingo 自己的視窗在最前面時不送（會貼到自己身上）。"""
        if not self.cfg["chat_send"] or msg.get("who") != "me" or msg.get("via") != "spoken" or not out.strip():
            return
        if oswin.foreground_is_self():
            return
        open_key, submit = self.cfg["chat_send_open"], bool(self.cfg["chat_send_enter"])

        def work():
            if oswin.send_chat(out, open_key, submit):
                self._note("chat_sent")
            else:
                self.log("chat send: could not type into the game")

        threading.Thread(target=work, daemon=True, name="chat-send").start()

    # ------------------------------------------------------------------ 2.16.0：遊戲和畫面

    # ------------------------------------------------------------------ 2.18.0：語音

    def _listen_extras(self, text: str, lang: str, unsure: bool, speaker, length: float) -> None:
        """對方說的每一句：講太快的提醒、聽到自我介紹就建議記住聲音。"""
        try:
            if self.cfg["slow_tip"]:
                why = self.pace.add(text, lang, unsure, float(length or 0.0))
                if why:
                    phrase = pace.ask_in(lang)
                    self.log(f"they seem to talk too fast ({why}); suggested asking them to slow down")
                    self.hub.publish("slow_tip", {"why": why, "phrase": phrase, "lang": lang})
                    self._note("slow_tip", phrase=phrase)
            if speaker and self.cfg["intro_suggest"] and self.cfg["tell_speakers"] and not self.private:
                n = int(speaker)
                if n not in self._intro_asked and not self._speaker_names.get(n):
                    name = introduce.name_in(text, lang)
                    if name:
                        self._intro_asked.add(n)
                        self.hub.publish("voice_name", {"speaker": n, "name": name, "remember": bool(self.cfg["remember_speakers"])})
                        self._note("intro_heard", name=name)
        except Exception:  # noqa: BLE001  提醒失敗不能讓字幕也沒有
            self.log(traceback.format_exc())

    def _voice_command(self, command: str) -> None:
        """對麥克風說的語音指令。"""
        self.log(f"voice command: {command}")
        try:
            if command == "pause":
                self.voice_paused = True
                self._end_live("loopback")
                self.hub.publish("voice_paused", True)
                self._note("vc_pause")
            elif command == "resume":
                self.voice_paused = False
                self.hub.publish("voice_paused", False)
                self._note("vc_resume")
            elif command == "again":
                self._note("vc_again" if self.retranslate_last() else "vc_again_none")
            elif command == "clear":
                self.clear_messages()
                self._note("vc_clear")
            elif command == "mark":
                self.mark_now()
            elif command in ("overlay", "copy"):
                self._hotkey_work(command)
        except Exception:  # noqa: BLE001
            self.log(traceback.format_exc())

    def set_voice_paused(self, on: bool) -> dict:
        """介面上的「繼續翻譯」按鈕（說了暫停之後）。"""
        self.voice_paused = bool(on)
        self.hub.publish("voice_paused", self.voice_paused)
        return {"ok": True, "paused": self.voice_paused}

    def retranslate_last(self) -> bool:
        """重翻對方說的（或畫面上讀到的）上一句：用最新的詞彙表和前後文再翻一次，排最前面。"""
        with self._lock:
            msg = next((m for m in reversed(self.messages) if m["who"] == "them" and not m.get("pending")), None)
        if not msg:
            return False
        self.translator.clear_cache()
        msg = dict(msg, pending=True, error="", why="", copied=False, back="")
        self._post(msg)
        self.jobs.put(msg, asked=True)
        return True

    def _session_items(self, voice_only: bool = False) -> list[dict]:
        with self._lock:
            items = [dict(m, length=self._msg_len.get(m["id"], 0.0)) for m in self.messages]
        return sessionlog.rows(items, voice_only)

    def mark_now(self) -> dict | None:
        """精華標記（2.18.0）：記下現在這個時間點和最新的一句，結束後列出來方便剪片。"""
        with self._lock:
            items = [dict(m, length=self._msg_len.get(m["id"], 0.0)) for m in self.messages]
            last = next((m for m in reversed(self.messages) if not m.get("error")), None)
        now = time.time()
        start = sessionlog.started_at(items) if items else now
        mark = {"id": int(now * 1000), "at": now, "offset": round(max(0.0, now - start), 1),
                "original": str(last["original"]) if last else "", "translated": str(last.get("translated") or "") if last else "",
                "msg": last["id"] if last else 0}
        with self._lock:
            self.marks = (self.marks + [mark])[-MAX_MARKS:]
            marks = list(self.marks)
        self.hub.publish("marks", marks)
        self._note("mark_done", time=sessionlog.clock(mark["offset"]))
        return mark

    def marks_action(self, action: str, mark_id: int = 0) -> dict:
        """add：標記現在；remove：刪掉一個；clear：全部清掉。"""
        if action == "add":
            self.mark_now()
        elif action == "remove":
            with self._lock:
                self.marks = [k for k in self.marks if k["id"] != int(mark_id or 0)]
        elif action == "clear":
            with self._lock:
                self.marks = []
        else:
            return {"ok": False}
        with self._lock:
            marks = list(self.marks)
        self.hub.publish("marks", marks)
        return {"ok": True, "marks": marks}

    def session_export(self, kind: str) -> dict:
        """這一場存成檔案（2.18.0）：md / docx = 通話紀錄（只有語音），srt = 有時間軸的字幕檔（全部翻譯）。"""
        if kind not in sessionlog.KINDS:
            return {"ok": False, "error": "kind"}
        items = self._session_items(voice_only=kind != "srt")
        if not items:
            return {"ok": False, "error": "empty"}
        with self._lock:
            marks = list(self.marks)
            start = sessionlog.started_at([dict(m, length=self._msg_len.get(m["id"], 0.0)) for m in self.messages])
        try:
            path = sessionlog.save(self.transcript_dir(), kind, items, marks, self.cfg["ui_lang"], start)
        except OSError as ex:
            self.log(f"saving the session failed: {ex!r}")
            return {"ok": False, "error": "save"}
        self.log(f"session saved as {kind}: {len(items)} lines")
        return {"ok": True, "path": str(path), "name": path.name, "count": len(items)}

    def _speed_pick(self) -> None:
        """自動選語音設定（2.18.0）：第一次用語音時，拿幾秒的聲音給語音模型辨識看看要多久，
        照速度選「比較快／比較準」和即時字幕。只改使用者還沒自己動過的設定，只測一次（設定裡可以重測）。"""
        try:
            import numpy as np

            from .core.audio import SR

            rec = self._ensure_recognizer()
            rng = np.random.default_rng(1)
            t = np.arange(int(SR * SPEED_SECONDS)) / SR
            sound = (0.02 * rng.standard_normal(len(t)) + 0.05 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)).astype(np.float32)
            started = time.monotonic()
            rec.transcribe(sound, [self.cfg["their_lang"] if self.cfg["their_lang"] in LANGS else "en"])
            spent = time.monotonic() - started
            speed = round(spent / SPEED_SECONDS, 3)
            mode, live = pick_speech(speed)
            changes = {"speech_picked": max(speed, 0.001)}
            if self.cfg["speech_mode"] == DEFAULTS["speech_mode"]:
                changes["speech_mode"] = mode
            if self.cfg["live_captions"] == DEFAULTS["live_captions"]:
                changes["live_captions"] = live
            self.log(f"speech speed: {spent:.2f}s for {SPEED_SECONDS:.0f}s of sound on {getattr(rec, 'device', 'cpu')}; picked {mode}, live captions {live}")
            self.cfg.set(**changes)
            rec.beam = BEAMS[self.cfg["speech_mode"]]
            self.hub.publish("config", self.cfg.snapshot())
            if mode == "accurate":
                self.notice("speech_picked_accurate")
            elif live:
                self.notice("speech_picked_fast")
            else:
                self.notice("speech_picked_slow")
        except Exception:  # noqa: BLE001  測不了就照預設
            self.log(traceback.format_exc())
        finally:
            self._speed_testing = False

    def speech_repick(self) -> dict:
        """設定頁的「重新測速」。"""
        if self._speed_testing:
            return {"ok": False}
        self.cfg.set(speech_picked=0.0, speech_mode=DEFAULTS["speech_mode"], live_captions=DEFAULTS["live_captions"])
        self._speed_testing = True
        threading.Thread(target=self._speed_pick, daemon=True, name="speed").start()
        return {"ok": True}

    def _screen_extra(self, color: str, region: dict | None) -> dict:
        """讀畫面那一則多帶的：頻道顏色、是不是劇情對話框。沒有就不帶（submit 的舊呼叫方式不變）。"""
        extra = {}
        tint = gametools.channel_of(color) if color and self.cfg["channel_colors"] else ""
        if tint:
            extra["tint"] = tint
        if (region or {}).get("story"):
            extra["story"] = True
        return extra

    def _screen_app(self) -> str:
        """讀畫面的區域跟著哪個程式（劇情書沒認出遊戲時，用它分本）。"""
        return next((str(r.get("app") or "") for r in self._regions_on() if r.get("app")), "")

    def _foreground_game(self, windows: list[dict], extra=()) -> str:
        return find_game(windows, self.glossary.games() | {str(e).lower() for e in extra})

    def check_profile(self, windows: list[dict] | None = None) -> str:
        """遊戲專屬版面：切到有存版面的遊戲時，套用它的浮動字幕版面、對方的語言、開著的讀取區域。回傳套用的遊戲。"""
        profiles = self.cfg["game_profiles"]
        if not profiles or not self.cfg["game_profile_auto"]:
            return ""
        windows = apps.windows() if windows is None else windows
        found = self._foreground_game(windows, profiles)
        if found == self._profile_game:
            return ""
        self._profile_game = found
        profile = profiles.get(found)
        if not profile:
            return ""
        self._apply_profile(profile)
        self.log(f"game profile applied: {found}")
        self.notice("profile_applied", game=game_name(found))
        return found

    def _apply_profile(self, profile: dict) -> None:
        if isinstance(profile.get("layout"), dict):
            self.overlay.apply_entry(profile["layout"])
        if profile.get("their_lang") in LANGS and profile["their_lang"] != self.cfg["their_lang"]:
            self.update_config({"their_lang": profile["their_lang"], "their_auto": False})
        ids = profile.get("regions")
        if isinstance(ids, list) and ids:
            regions = [dict(r, on=r["id"] in ids) for r in self.cfg["regions"]]
            if regions != self.cfg["regions"] and any(r["on"] for r in regions):
                self.cfg.set(regions=regions)
                self._regions_changed()

    def profile_action(self, action: str, exe: str = "") -> dict:
        """save：把現在的版面、語言、開著的區域存給 exe（沒給就用現在認出的遊戲）；remove：刪掉；apply：現在套用。"""
        exe = (exe or self.game or self._foreground_game(apps.windows())).lower()[:120]
        if not exe:
            return {"ok": False, "error": "no_game"}
        profiles = dict(self.cfg["game_profiles"])
        if action == "save":
            if len(profiles) >= 30 and exe not in profiles:
                return {"ok": False, "error": "full"}
            profiles[exe] = {"layout": self.overlay.current_entry(), "their_lang": self.cfg["their_lang"],
                             "regions": [r["id"] for r in self.cfg["regions"] if r.get("on")]}
            self._profile_game = exe
        elif action == "remove":
            profiles.pop(exe, None)
        elif action == "apply" and exe in profiles:
            self._apply_profile(profiles[exe])
            return {"ok": True, "exe": exe}
        else:
            return {"ok": False, "error": "unknown"}
        self.cfg.set(game_profiles=profiles)
        self.hub.publish("config", self.cfg.snapshot())
        return {"ok": True, "exe": exe, "name": game_name(exe)}

    def check_fullscreen(self, windows: list[dict] | None = None) -> bool:
        """遊戲用獨佔全螢幕：浮動字幕蓋不上去、讀畫面抓到黑的。開著浮動字幕或讀畫面時提醒一次（這次開遊戲）。"""
        if not self.cfg["fullscreen_warn"]:
            return False
        playing = bool(self.game or self._foreground_game(windows if windows is not None else []))
        if not playing:
            self._fullscreen_told = False
            return False
        if self._fullscreen_told or not (self.cfg["overlay"] or self.sources["screen"]["on"]) or not oswin.exclusive_fullscreen():
            return False
        self._fullscreen_told = True
        self.log("the game is in exclusive fullscreen; told the user to use borderless")
        self.notice("fullscreen_exclusive")
        return True

    def template_apply(self) -> dict:
        """常見遊戲範本：照現在開著的遊戲視窗，加一個框好的聊天區（跟著那個遊戲的視窗走）。"""
        windows = apps.windows()
        have = gametools.templates()
        win = next((w for w in windows if str(w.get("exe") or "").lower() in have and not w.get("minimized")), None)
        if not win:
            return {"ok": False, "error": "no_game", "games": sorted({game_name(e) for e in have})}
        exe = str(win["exe"]).lower()
        if len(self.cfg["regions"]) >= MAX_REGIONS:
            return {"ok": False, "error": "full"}
        rid = "t" + str(int(time.time() * 10) % 1000000)
        region = gametools.template_region(exe, win["rect"], rid, game_name(exe))
        if not region:
            return {"ok": False, "error": "no_game"}
        self.cfg.set(regions=[*self.cfg["regions"], region])
        self._regions_changed()
        self.hub.publish("config", self.cfg.snapshot())
        self.log(f"template region for {exe}: {region}")
        return {"ok": True, "game": game_name(exe), "id": rid, "story": bool(region.get("story"))}

    def find_chat(self) -> dict:
        """自動找聊天框：整個主螢幕讀一次字，找最像聊天框的一塊，加成一個新的聊天區（先關著，給使用者看過再開）。"""
        import numpy as np

        from .core.watcher import blank_covers

        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}
        mon = (oswin.monitors() or [{"left": 0, "top": 0, "width": 1920, "height": 1080}])[0]
        place = {k: int(mon[k]) for k in ("left", "top", "width", "height")}
        try:
            grab = getattr(self, "grab", None)
            if grab is None:
                import mss

                with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                    bgr = np.ascontiguousarray(np.asarray(sct.grab(place))[:, :, :3])
            else:
                bgr = np.ascontiguousarray(grab(place))
            covers = self.capture_mask()
            if covers:
                bgr = blank_covers(bgr, place, covers)
            box = gametools.find_chat_box(self._load_ocr().read(bgr), place["width"], place["height"])
        except Exception as ex:  # noqa: BLE001
            self.log(f"find chat box failed: {ex!r}")
            return {"ok": False, "error": "failed"}
        if not box:
            return {"ok": False, "error": "not_found"}
        if len(self.cfg["regions"]) >= MAX_REGIONS:
            return {"ok": False, "error": "full"}
        rid = "f" + str(int(time.time() * 10) % 1000000)
        region = {"id": rid, "name": "", "left": place["left"] + box[0], "top": place["top"] + box[1], "width": box[2],
                  "height": box[3], "on": False, "app": "", "anchor": None}
        self.cfg.set(regions=[*self.cfg["regions"], region])
        self._regions_changed()
        self.hub.publish("config", self.cfg.snapshot())
        return {"ok": True, "id": rid, "box": [region["left"], region["top"], box[2], box[3]]}

    def wall_action(self, action: str) -> dict:
        """副螢幕字幕牆：open 在 wall_monitor 那個螢幕開一個全螢幕的字幕視窗（用直播字幕頁），close 關掉。"""
        if action == "close":
            self.stream.wall_on = False
            if self.window and hasattr(self.window, "close_wall"):
                self.window.close_wall()
            self.stream.apply()
            return {"ok": True}
        if action != "open":
            return {"ok": False, "error": "unknown"}
        monitors = oswin.monitors() or []
        if len(monitors) < 2:
            return {"ok": False, "error": "one_screen"}
        mon = monitors[min(max(1, int(self.cfg["wall_monitor"])), len(monitors) - 1)]
        self.stream.wall_on = True
        self.stream.apply()
        url = self.stream.url(wall=True)
        if not url:
            self.stream.wall_on = False
            return {"ok": False, "error": "no_port"}
        rect = [mon["left"], mon["top"], mon["width"], mon["height"]]
        if self.window and hasattr(self.window, "open_wall") and self.window.open_wall(url, rect):
            return {"ok": True}
        return {"ok": bool(oswin.open_path(url)), "browser": True}      # 介面是用瀏覽器開的：字幕牆也用瀏覽器開

    def story_state(self, game: str = "", offset: int = 0) -> dict:
        games = [dict(g, name=game_name(g["game"]) if g["game"] else "") for g in self.storybook.games()]
        pick = game or (games[0]["game"] if games else "")
        return {"games": games, **self.storybook.read(pick, offset)}

    def story_action(self, action: str, game: str = "") -> dict:
        if action == "export":
            path = self.storybook.export(game, game_name(game) if game else "LiveLingo", self.transcript_dir())
            if not path:
                return {"ok": False, "error": "empty"}
            oswin.open_path(str(path.parent))
            return {"ok": True, "name": path.name}
        if action == "clear":
            return {"ok": True, "removed": self.storybook.clear(game)}
        return {"ok": False, "error": "unknown"}

    def album_action(self, action: str, aid: str = "") -> dict:
        if action == "remove":
            ok = self.album.remove(aid)
            self.hub.publish("album", {"items": self.album.list()})
            return {"ok": ok}
        return {"ok": True, "items": self.album.list()}

    def _album_keep(self, bgr, shown: list[tuple[int, str]]) -> None:
        """框選翻譯：等那幾段都翻好（最多 60 秒），連截圖一起存進相簿。"""
        if not self.cfg["snap_album"] or self.private or not shown:
            return
        ids = {mid for mid, _ in shown}

        def work():
            end = time.time() + 60
            while time.time() < end and not self.closing.is_set():
                with self._lock:
                    msgs = {m["id"]: m for m in self.messages if m["id"] in ids}
                if all(mid in msgs and not msgs[mid].get("pending") for mid in ids):
                    break
                time.sleep(0.3)
            with self._lock:
                msgs = {m["id"]: m for m in self.messages if m["id"] in ids}
            pairs = [(text, (msgs.get(mid) or {}).get("translated") or "") for mid, text in shown]
            if self.album.add(bgr, pairs, self.game):
                self.hub.publish("album", {"items": self.album.list()})

        threading.Thread(target=work, daemon=True, name="album").start()

    # ------------------------------------------------------------------ 2.15.0：沒把握的句子、網路用語註解、縮寫提醒

    def _hidden(self, msg: dict) -> bool:
        return bool(msg.get("unsure") and not msg.get("pending") and msg.get("who") == "them" and self.cfg["unsure_view"] == "hide")

    def _still_unsure(self, msg: dict, target: str, out: str) -> bool:
        """不確定時再翻一次比對：換一種寫法再翻，兩次意思差不多（字有一半以上一樣）就不標沒把握。"""
        try:
            again = self.translator.second_opinion(msg["original"], target, msg.get("lang"))
        except Exception as ex:  # noqa: BLE001
            self.log(f"second translation for an unsure sentence failed: {ex!r}")
            return True
        same = bool(again) and similarity(again, out) >= 0.5
        self.log(f"unsure sentence checked twice: {'same meaning, mark removed' if same else 'different, still marked'}")
        return not same

    def _slang_notes(self, msg: dict, target: str) -> list[list[str]]:
        if not self.cfg["slang_notes"] or msg.get("who") == "me" or not msg.get("lang"):
            return []
        try:
            found = self.terms.find(msg["original"], family_of(msg["lang"]) or msg["lang"], target)
        except Exception:  # noqa: BLE001
            return []
        out: list[list[str]] = []
        for term, meaning in found:
            if term in msg["original"] and term not in msg.get("translated", "") and [term, meaning] not in out:
                out.append([term, meaning])
        return out[:3]

    def _watch_abbr(self, msg: dict) -> None:
        if not self.cfg["abbr_suggest"] or self.private or msg.get("who") == "me" or msg.get("via") not in ("screen", "heard"):
            return
        known = [*self.cfg["abbr_skip"], *self.cfg["keep_words"], *self._words_now()]
        known += [t for t, _ in (self.terms.ko_zh if hasattr(self.terms, "ko_zh") else [])]
        word = self.abbr_watch.add(msg.get("original", ""), known)
        if word:
            self.hub.publish("abbr_suggest", {"word": word, "lang": msg.get("lang") or ""})

    def abbr_answer(self, word: str, meaning: str = "") -> dict:
        """縮寫提醒的回答：有寫意思就加進詞彙表（縮寫 → 意思）；沒寫 = 不用，記下來不再問。"""
        word = " ".join(str(word or "").split())[:keepwords.MAX_LEN]
        meaning = " ".join(str(meaning or "").split())[:80]
        if not word:
            return {"ok": False}
        if meaning:
            return {"ok": self.glossary_add(word, meaning)}
        self.update_config({"abbr_skip": [*self.cfg["abbr_skip"], word]})
        return {"ok": True}

    def _watch_names(self, msg: dict) -> None:
        """名字建議：對方的話（畫面上的、聽到的）裡一直出現、像遊戲 ID 的字，第 3 次出現時問一次要不要固定不翻。"""
        if not self.cfg["name_suggest"] or self.private or msg.get("who") == "me" or msg.get("via") not in ("screen", "heard"):
            return
        skip = [*self.cfg["keep_words"], *self.cfg["name_skip"], *self.glossary.as_dict(self._game_words())]
        name = self.name_watch.add(msg.get("original", ""), skip)
        if name:
            self.hub.publish("name_suggest", {"name": name})

    def name_answer(self, name: str, keep: bool) -> dict:
        """名字建議的回答：keep = 加進不翻的字；不是名字 = 記下來不再問。"""
        name = " ".join(str(name or "").split())[:keepwords.MAX_LEN]
        if not name:
            return {"ok": False}
        key = "keep_words" if keep else "name_skip"
        self.update_config({key: [*self.cfg[key], name]})
        return {"ok": True, "keep_words": self.cfg["keep_words"]}

    def set_private(self, on: bool) -> dict:
        """隱私暫停：翻譯照常，但不記歷史、不記翻過的句子和看過的作品、不算使用數字、字幕記錄不寫、不數名字。
        不存進設定檔：重開程式一定回到平常（免得忘了關，之後什麼都沒記到）。"""
        self.private = bool(on)
        self.log(f"private pause {'on' if self.private else 'off'}")
        self.hub.publish("private", {"on": self.private})
        return {"ok": True, "on": self.private}

    def _auto_watch(self) -> set[str]:
        """會觸發「開遊戲就開始翻」的程式（小寫檔名）：框選區跟著的、只聽的、認得的遊戲和加過專用詞的程式。"""
        from .terms import GAMES

        exes = {str(r.get("app") or "").lower() for r in self.cfg["regions"]}
        exes |= {str(a).lower() for a in self.cfg["listen_apps"]}
        exes |= {g.lower() for g in GAMES} | self.glossary.games()
        exes.discard("")
        return exes

    def check_auto_start(self, windows: list[dict] | None = None) -> list[str]:
        """開遊戲就開始翻：看的程式從「一個都沒開」變成「有開」就打開讀畫面（有框過區域的話）和聽對方；
        全部關掉就把剛剛自己打開的關掉（使用者自己開的不動）。回傳這次打開或關掉的來源。"""
        if not self.cfg["game_start"] or self.phase != "ready":
            return []
        watch = self._auto_watch()
        try:
            windows = apps.windows() if windows is None else windows
        except Exception:  # noqa: BLE001
            return []
        running = {str(w.get("exe") or "").lower() for w in windows} & watch
        was, self._auto_apps = self._auto_apps, running
        changed: list[str] = []
        if running and not was:
            for key in ("screen", "listen"):
                if key == "screen" and not self.cfg["regions"]:
                    continue
                if not self.sources[key]["on"] and not self.sources[key]["busy"]:
                    self.set_source(key, True)
                    self._auto_started.append(key)
                    changed.append(key)
            if changed:
                name = apps.display_name(sorted(running)[0])
                self.log(f"auto start {changed} because {sorted(running)} opened")
                self._note("auto_started", app=name)
        elif was and not running and self._auto_started:
            for key in self._auto_started:
                if self.sources[key]["on"]:
                    self.set_source(key, False)
                    changed.append(key)
            self._auto_started = []
            if changed:
                self.log(f"auto stop {changed}: watched programs closed")
                self.notice("auto_stopped")
        return changed

    def _read_heard(self, msg: dict, target: str, out: str) -> None:
        """念對方的話給我聽：對方說的（有開「畫面上的也念」的話，畫面上讀到的聊天也算）翻成我的語言之後念到我選的裝置。"""
        if not self.cfg["speak_heard"] or msg.get("who") == "me" or not out.strip() or target != self.cfg["my_lang"]:
            return
        if msg.get("via") == "heard" or (msg.get("via") == "screen" and self.cfg["speak_heard_screen"]):
            if not self.reader.say(out, target):
                self._speak_state(error="unavailable")

    def _read_done(self, result: dict) -> None:
        if not result.get("ok") and result.get("error") == "no_voice" and self._speak.get("heard_error") != "no_voice":
            self.notice("speak_no_voice", lang=result.get("lang", ""))
        self._speak_state(heard_error=result.get("error") or "", heard_voice=result.get("voice", "") if result.get("ok") else "")

    def quality_action(self, action: str) -> dict:
        """翻譯自我檢查：start 開始（在背景跑，一句一句送 quality 事件）/ stop 停下來。"""
        if action == "stop":
            self.quality.stop()
            return {"ok": True}
        if action != "start":
            return {"ok": False, "error": "unknown"}
        if self.phase != "ready":
            return {"ok": False, "error": "not_ready"}

        def one(case: dict) -> str:
            self._wake_engine()
            if not self._engine_ok.is_set():
                self._engine_ok.wait(300)
            self.translator.model = self.mode.get("model", "")
            return self.translator.translate(case["src"], case["target"], case.get("lang"), fresh=True)

        started = self.quality.start(one, __version__, self.mode.get("model", ""))
        return {"ok": started, **({} if started else {"error": "running"})}

    @staticmethod
    def scenarios_state() -> dict:
        """首頁的情境卡：每一張會改哪些設定、開哪些來源（介面照現在的設定判斷哪一張亮著）。"""
        return {name: {"config": dict(cfg), "sources": dict(sources)} for name, (cfg, sources) in SCENARIOS.items()}

    def apply_scenario(self, name: str) -> dict:
        """情境卡：一次換好一整組設定，打開或關掉要用的來源。只改顯示方式和要開哪些來源，不碰語言、裝置、框好的區域。"""
        if name not in SCENARIOS:
            return {"ok": False, "error": "unknown"}
        cfg, sources = SCENARIOS[name]
        self.update_config(dict(cfg))
        need_region = sources.get("screen") and not self.cfg["regions"]
        for key, on in sources.items():
            if bool(self.sources[key]["on"]) != on and not self.sources[key]["busy"]:
                self.set_source(key, on)
        self.log(f"scenario {name}")
        return {"ok": True, "name": name, "need_region": bool(need_region)}

    # ------------------------------------------------------------------ 念出我的譯文（2.9.0）

    def _speak_mine(self, msg: dict, target: str, out: str) -> None:
        if self.cfg["speak_out"] and msg.get("who") == "me" and target != self.cfg["my_lang"] and out.strip():
            if not self.speaker.say(out, target):
                self._speak_state(error="unavailable")

    def _speak_state(self, **changes) -> None:
        self._speak.update(changes)
        self.hub.publish("speak", self.speak_state())

    def speak_state(self) -> dict:
        return dict(self._speak, available=self.speaker.available())

    def _spoke(self, result: dict) -> None:
        if result.get("ok"):
            self._speak_state(voice=result.get("voice", ""), lang=result.get("lang", ""), error="")
            return
        error = result.get("error") or "failed"
        if error == "no_voice":
            if self._speak.get("error") != "no_voice" or self._speak.get("lang") != result.get("lang"):
                self.notice("speak_no_voice", lang=result.get("lang", ""))
        elif self._speak.get("error") != error:
            self.notice("speak_failed", error=error)
        self._speak_state(error=error, lang=result.get("lang", ""))

    def speak_test(self) -> dict:
        """設定頁的「試聽」：用對方的語言念一句範例。"""
        from .core.speak import SAMPLES

        lang = self.cfg["their_lang"]
        ok = self.speaker.say(SAMPLES.get(lang, SAMPLES["en"]), lang)
        return {"ok": ok, "lang": lang}

    def speak_voices(self) -> dict:
        voices = self.speaker.voices()
        return {"ok": True, "voices": voices}

    def _voice_script(self, kind: str, text: str, lang: str | None, own: tuple[str, str] | None = None) -> str:
        """語音辨識給的中文有時是繁體有時是簡體：照說話的人那一邊選的語言統一。
        own：這個說話者自己設的（主語言, 副語言）；裡面有中文就照他設的那一種。"""
        if lang != "zh":
            return text
        from .core.translate import to_script

        mine = next((code for code in own or () if code and LANGS[code]["family"] == "zh"), "")
        if mine:
            return to_script(text, mine)
        speaker, other = ((self.cfg["their_lang"], self.cfg["my_lang"]) if kind == "loopback"
                          else (self.cfg["my_lang"], self.cfg["their_lang"]))
        return to_script(text, speaker if LANGS[speaker]["family"] == "zh" else other)

    def _on_speaking(self, kind: str, speaking: bool) -> None:
        self._source(SOURCE_OF[kind], speaking=speaking)

    def _voice_error(self, kind: str, ex: Exception) -> None:
        key = SOURCE_OF[kind]
        self.log(f"{kind} capture failed: {ex!r}")
        tries = self._voice_tries.get(key, 0)
        # 本來收得好好的、用到一半才斷（耳機拔掉、藍牙斷線、Windows 換了預設裝置）：自己重新連接，不要等使用者發現沒字幕了才去按
        running = self._voice_open.pop(key, False) or tries > 0
        self._stop_voice(key)
        if running and tries < len(RECONNECT_WAITS) and self.cfg["sources"].get(key) and not self.closing.is_set():
            self._voice_tries[key] = tries + 1
            self._source(key, on=True, busy=False, speaking=False, quiet="", error="", lost=True)
            if not tries:
                self.notice("device_lost")
            timer = threading.Timer(RECONNECT_WAITS[tries], self._voice_retry, args=(key,))
            timer.daemon = True
            timer.start()
            return
        self._voice_tries.pop(key, None)
        self._source(key, on=False, busy=False, speaking=False, quiet="", lost=False, error=str(ex) or type(ex).__name__)

    def _voice_retry(self, key: str) -> None:
        if self.closing.is_set() or not self.sources[key]["lost"] or not self.sources[key]["on"]:
            return      # 等的時候使用者自己關掉了
        self._start_voice(key, again=True)

    # ------------------------------------------------------------------ 來源：開關

    def set_source(self, key: str, on: bool) -> None:
        if key not in self.sources or self.sources[key]["busy"]:
            return
        wanted = dict(self.cfg["sources"])
        wanted[key] = bool(on)
        self.cfg.set(sources=wanted)
        if on and self.phase != "ready":
            self.notice("not_ready")      # 已經記下來了，翻譯引擎準備好之後會自動打開
            return
        if key == "screen":
            if on and not self.cfg["regions"]:
                self.select_region(then_enable=True)
            elif on and not self._regions_on():      # 有框過，但全部關著：把第一個打開
                regions = [dict(r) for r in self.cfg["regions"]]
                regions[0]["on"] = True
                self.cfg.set(regions=regions)
                self.hub.publish("config", self.cfg.snapshot())
                threading.Thread(target=self._start_screen, daemon=True).start()
            elif on:
                threading.Thread(target=self._start_screen, daemon=True).start()
            else:
                if self.watcher:
                    self.watcher.paused = True
                self.inplace.reset()
                self._source("screen", on=False, error="")
        elif on:
            threading.Thread(target=self._start_voice, args=(key,), daemon=True).start()
        else:
            self._voice_tries.pop(key, None)
            self._voice_open.pop(key, None)
            self._stop_voice(key)
            self._source(key, on=False, speaking=False, error="", quiet="", lost=False)

    # ------------------------------------------------------------------ 聲音裝置

    def find_mic(self) -> bool:
        """「自動找麥克風」：同時聽所有麥克風幾秒，使用者說話時最大聲的那一支就是他在用的，直接換過去。"""
        if self._finding_mic:
            return False
        self._finding_mic = True
        self._find_cancel.clear()
        threading.Thread(target=self._find_mic, daemon=True, name="find-mic").start()
        return True

    def cancel_find_mic(self) -> None:
        self._find_cancel.set()

    def _find_mic(self, seconds: float = 5.0) -> None:
        import numpy as np

        from .core.speech import QUIET

        try:
            try:
                mics = self.audio.devices()["microphones"][:8]
            except Exception as ex:  # noqa: BLE001
                self.log(traceback.format_exc())
                self.hub.publish("find_mic", {"state": "none", "error": str(ex), "blocked": False, "count": 0})
                return
            self.hub.publish("find_mic", {"state": "listening", "seconds": seconds})
            captures = []
            for mic in mics:
                try:
                    cap = self.audio.capture("mic", mic["id"])
                    cap.start()
                    captures.append((mic, cap))
                except Exception:  # noqa: BLE001
                    self.log(traceback.format_exc())
            loud: dict[str, list[float]] = {mic["id"]: [] for mic, _ in captures}
            end = time.time() + seconds
            while time.time() < end and not self.closing.is_set() and not self._find_cancel.is_set():
                time.sleep(0.1)
                for mic, cap in captures:
                    try:
                        while True:
                            block = cap.q.get_nowait()
                            if len(block):
                                loud[mic["id"]].append(float(np.sqrt(np.mean(np.square(block)))))
                    except queue.Empty:
                        pass
            for _, cap in captures:
                cap.stop()
            if self._find_cancel.is_set():      # 使用者按了取消：什麼都不換
                self.hub.publish("find_mic", {"state": "cancelled"})
                return

            def score(values: list[float]) -> float:
                """說話時的音量：最大聲的那五分之一的平均（整段平均會被沒說話的時間拉低）。"""
                top = sorted(values, reverse=True)[: max(1, len(values) // 5)]
                return sum(top) / len(top) if top else 0.0

            scores = {mic["id"]: score(loud[mic["id"]]) for mic, _ in captures}
            self.log("find mic: " + "; ".join(f"{mic['name']} = {scores[mic['id']]:.4f}" for mic, _ in captures))
            best = max(captures, key=lambda mc: scores[mc[0]["id"]], default=None)
            if best is None or scores[best[0]["id"]] < QUIET * 4:
                self.hub.publish("find_mic", {"state": "none", "blocked": bool(oswin.mic_privacy_blocked()),
                                              "count": len(mics)})
                return
            mic = best[0]
            changed = self.sources["mic"]["device"] != mic["name"] or not self.sources["mic"]["on"]
            before = {"id": self.cfg["mic_device"], "name": self.cfg["mic_name"]}      # 讓使用者可以換回去
            self.update_config({"mic_device": mic["id"], "mic_name": mic["name"]})
            if not self.sources["mic"]["on"]:
                self.set_source("mic", True)
            elif self.sources["mic"]["device"] != mic["name"] and self.cfg["mic_device"] == mic["id"]:
                self._restart_voice("mic")
            self.hub.publish("find_mic", {"state": "found", "name": mic["name"], "changed": changed,
                                          "previous": before if before["id"] != mic["id"] else None})
        finally:
            self._finding_mic = False

    def open_mic_settings(self) -> bool:
        return oswin.open_mic_settings()

    def devices(self) -> dict:
        try:
            found = self.audio.devices()
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            return {"ok": False, "error": str(ex), "speakers": [], "microphones": []}
        cfg = self.cfg
        return {"ok": True, **found, "speaker": cfg["speaker_device"], "microphone": cfg["mic_device"]}

    def set_meters(self, on: bool) -> None:
        """設定頁打開時，每個裝置各開一條收音，把音量送給介面畫音量條。"""
        from .meters import LevelMeters

        if self.meters:
            self.meters.stop()
            self.meters = None
        if on:
            try:
                found = self.audio.devices()
            except Exception:  # noqa: BLE001
                return
            self.meters = LevelMeters(self.audio, found, lambda levels: self.hub.publish("levels", levels))
            self.meters.start()

    # ------------------------------------------------------------------ 視窗

    def focus(self) -> None:
        """使用者又點了一次桌面捷徑：把已經開著的視窗叫到最前面。"""
        if self.window:
            self.window.focus()

    def set_chrome(self, caption: str, text: str, dark: bool) -> bool:
        """介面換主題時，把標題列的顏色一起換掉。"""
        import re

        if not (re.fullmatch(r"#[0-9A-Fa-f]{6}", caption or "") and re.fullmatch(r"#[0-9A-Fa-f]{6}", text or "")):
            return False
        self.chrome = (caption, text, bool(dark))
        return bool(self.window and self.window.set_chrome(*self.chrome))

    def set_view(self, view: str) -> None:
        """main（主畫面）/ settings（設定）/ wizard（第一次設定）。"""
        if view not in ("main", "settings", "wizard"):
            return
        previous, self.view = self.view, view
        if self.window:
            self.window.apply_view(view, previous)
        if view != "settings" and self.meters:
            self.set_meters(False)
        self.hub.publish("view", {"view": view})

    # ------------------------------------------------------------------ 結束

    def shutdown(self) -> None:
        if self.closing.is_set():
            return
        self.tray.stop()
        self.hotkeys.stop()
        self.gamepad.stop()
        self.stream.stop()
        self.speaker.stop()
        for child in (self._quick, self._reply_child, self._phrase_child, self._card_child):
            if child is not None and child.poll() is None:
                child.kill()
        if self.transcript.on:      # 記錄到一半就關程式：先存起來，不要弄丟
            try:
                self.transcript.stop(self.transcript_dir(), self.cfg["ui_lang"], bool(self.cfg["transcript_original"]))
            except OSError as ex:
                self.log(f"subtitle log could not be saved: {ex!r}")
        self.closing.set()
        self._download_cancel.set()
        self.usage.flush()
        self._voices_save()
        if self.meters:
            self.meters.stop()
        self.inplace.close()
        if self._subs_job is not None:
            self._subs_job.cancel.set()
        self.overlay.stop()
        self._stop_watcher()
        for key in list(self.pipelines):
            self._stop_voice(key)
        if self.engine:
            self.engine.stop()
        self.cfg.save()
