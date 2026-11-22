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

from . import __version__, apps, assets, changes, chatpaint, gpu, guide, oswin, paths, report, storage, updater, voicebook
from .config import (ACCENTS, DEFAULTS, EDITABLE, MAX_LAYOUTS, MAX_REGIONS, OVERLAY_FORMS, OVERLAY_LOOK, THEMES, UI_LANGS, Config,
                     Glossary, Lessons)
from .core.engine import Engine
from .core.romanize import romanize
from .core.translate import LANGS, MAX_TEXT, Stop, Stopped, Translator, detect_language, numbers_lost, similarity, why
from .history import DAYS as HISTORY_DAYS
from .jobs import Jobs
from .history import History
from .hotkeys import ACTIONS as HOTKEY_ACTIONS
from .hotkeys import NOTES as HOTKEY_NOTES
from .hotkeys import Hotkeys
from .hotkeys import normalize as hotkey_normalize
from .overlay import BAR_LOOK, Overlay
from .paths import self_command
from .terms import PACKS, TermPack
from .transcript import Transcript
from .tray import Tray
from .usage import Usage

VOICE_KIND = {"listen": "loopback", "mic": "mic"}
SOURCE_OF = {"loopback": "listen", "mic": "mic"}
VOICE_FAMILIES = ("ko", "zh", "en", "ja")      # 語音辨識認得、我們也翻得了的語言


# 「靈敏度」1 到 5 各是多少的語音偵測門檻（越低越容易把聲音當成說話）
VAD_THRESHOLD = {1: 0.7, 2: 0.6, 3: 0.5, 4: 0.4, 5: 0.3}
PARTIAL_EVERY = 0.08      # 譯文邊翻邊顯示：最快多久更新一次畫面（秒）
VOICES_SAVE_EVERY = 30.0      # 記住的聲音：聲紋跟著新的句子變了之後，最快多久存一次（秒）
INSTALLER_RETRY = (20.0, 60.0, 300.0)      # 剛更新完刪不掉的安裝檔（安裝程式還沒結束）：隔這麼久再試（秒）
RECONNECT_WAITS = (2, 4, 8, 15, 30, 30, 30)      # 收音裝置斷線後，隔幾秒再試一次；全部試完（大約兩分鐘）還不行才放棄
BEAMS = {"fast": 1, "accurate": 5}      # 語音辨識「比較快／比較準」各用多寬的搜尋
SETTINGS_KIND = "livelingo-settings"      # 匯出檔裡用來認「這是 LiveLingo 的設定檔」
# 不屬於「設定」的東西：視窗大小位置、第一次設定做過了沒、哪些來源現在開著、大檔案搬去哪。匯出、匯入、恢復預設都不碰
SETTINGS_SKIP = ("window", "layout", "setup_done", "tray_hint_shown", "region", "sources", "store_dir", "store_leftover")
# 恢復預設時留著不動的：語言、框好的聊天區、自己存的版面、字幕資料夾（這些是花力氣弄好的，不是「調壞了的設定」）
RESET_KEEP = ("ui_lang", "my_lang", "their_lang", "regions", "overlay_layouts", "transcript_dir")
ADVANCED_RANGE = {"interval": (0.2, 5.0), "ocr_scale": (1.0, 3.0), "whisper_threads": (1, 32), "threads": (0, 64)}
LIBRARY_KIND = "livelingo-library"      # 匯出檔裡用來認「這是 LiveLingo 的詞彙檔」


REVIVE_TIMES = 3            # 翻譯引擎自己停掉時，最多自動重開幾次……
REVIVE_WINDOW = 600.0       # ……在這麼多秒之內；超過就不再試


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
    """真的音訊裝置。測試時會換成假的。"""

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

        return Capture(kind, on_error=on_error, device=device, on_open=on_open)

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
        self.store = storage.store_dir(data, self.cfg["store_dir"])      # 大檔案（模型、翻譯引擎）放的地方，可以搬到別的磁碟
        self._move: dict | None = None      # 正在搬大檔案：{"to", "done", "total"}
        self._move_cancel = threading.Event()
        self._move_lock = threading.Lock()
        self.glossary = Glossary(data)
        self.lessons = Lessons(data)      # 使用者教過的整句翻法
        self.terms = TermPack(self.cfg["term_packs"])
        self.audio = audio or RealAudio()
        from .core import audio as audio_core

        audio_core.log = self.log      # 收音出錯的細節寫進紀錄檔
        self.gpu = {"name": None, "vram_mb": 0, "tier": "cpu"}
        self.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        self.fallback = False          # 高品質開不起來，自動退回標準
        self.engine: Engine | None = None
        self.translator = Translator("http://127.0.0.1:8177", glossary=self.glossary.as_dict,
                                     tone=self.cfg["tone"], hints=self._hints, lessons=self.lessons.list)
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
        self._find_cancel = threading.Event()
        self._update_choices: queue.Queue = queue.Queue()      # 使用者對「有新版本」的回答
        self._update_stop = threading.Event()
        self.messages: deque[dict] = deque(maxlen=200)
        self.jobs = Jobs()             # 翻譯的排隊：我自己的句子先翻，螢幕上讀到的排太久就略過（jobs.py）
        self._skipped = 0              # 略過了幾則還沒告訴使用者
        self._skip_told = 0.0
        self._engine_ok = threading.Event()      # 沒亮 = 翻譯引擎掛了，正在自動重開（翻譯先等它）
        self._engine_ok.set()
        self._revive_lock = threading.Lock()
        self._revivals: list[float] = []         # 最近幾次自動重開的時間
        self._retries: dict[int, int] = {}       # 引擎掛掉時正在翻的句子：補翻過幾次
        self._error_told = 0.0                   # 上一次在畫面上說「有一部分出了錯」的時間
        self.window = None             # 視窗控制（window.py 會放進來）
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
        self.usage = Usage(self.data, bool(self.cfg["usage_keep"]))      # 每天幾句、失敗幾次、平均等多久（只有數字）
        self._counted: set[int] = set()      # 已經算進使用數字的訊息（重翻不再算）
        self.tray = Tray(self)
        self.hotkeys = Hotkeys(self._hotkey, on_state=lambda states: self.hub.publish("hotkeys", self.hotkeys_state()))
        self._quick = None             # 快速輸入框開著的時候是那個小程式
        self._their_guess = ""      # 自動偵測：上一句聽起來像哪個別的語言（還不夠確定）
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
        self._speaker_run = 1      # 按一次「全部重新分辨」加一：之前標的編號就不算數了
        self._black_regions: set[str] = set()      # 抓到的畫面是全黑的聊天區（遊戲用全螢幕時會這樣）
        self._lock = threading.RLock()
        self.started = time.time()
        self._report = ""             # 上一次做給使用者看的問題報告（存檔、複製用的就是這一份）
        self._download_cancel = threading.Event()
        self._selecting = False
        self.on_quit = lambda: None    # 請視窗關閉、讓程式正常結束（介面按離開）
        self.exit_now = lambda: self.on_quit()   # 立刻結束整個程式（更新時用；__main__ 會換成真的）
        self.overlay = Overlay(self)   # 浮動留言

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
            "lessons": self.lessons.list(),
            "langs": {k: v["family"] for k, v in LANGS.items()},
            "ui_langs": list(UI_LANGS),
            "can_window": bool(self.window or self.can_window),
            "view": self.view,
            "overlay": self.overlay.state(),
            "record": self.record_state(),
            "hotkeys": self.hotkeys_state(),
            "history": self.history.state(),
            "settings_backup": self.settings_backup(),
            "changes": self.changes,                 # 每一版改了什麼（關於頁）
            "updated_from": self.updated_from,       # 剛自動更新（或退回）完第一次開：之前的版本號（只說一次）
            "update": self.update_state(),
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

    def _boot(self) -> None:
        try:
            self._installers_cleanup()
            if self._update_if_needed():
                return
            self.overlay.apply()
            self.start_hotkeys()
            self._storage_cleanup()
            self.gpu = gpu.detect()
            self._speech_engine()      # 先把「語音辨識用顯示卡」現在的狀態算好，介面才知道要不要顯示下載
            self.speakers_load()       # 有開「分辨說話者」：聲紋模型先準備好，設定頁一打開就看得到記住的人
            self._prepare()
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self.set_phase("error", message=str(ex))

    def update_choice(self, choice: str) -> None:
        """介面上按了「現在更新」（now）、「這次先不要」（later）或下載中的「取消」（cancel）。"""
        if choice in ("now", "later", "cancel"):
            self._update_choices.put(choice)
            if choice != "now":
                self._update_stop.set()

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
            return False
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
        return bool(self.phase == "ready" and engine and engine.proc is not None and engine.proc.poll() is not None)

    def _engine_guard(self) -> None:
        while not self.closing.wait(3):
            try:
                if self._engine_gone():
                    self._revive_engine()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())

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
        """換了用語包：重新載入，提示前面那句話也跟著換（沒有選英雄聯盟就不說是在聊英雄聯盟）。"""
        from .core.translate import HINT_INTRO, HINT_INTRO_CHAT

        self.terms = TermPack(self.cfg["term_packs"])
        self.translator.hint_intro = HINT_INTRO if "lol" in self.terms.names else HINT_INTRO_CHAT
        self.translator.clear_cache()

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
        self.overlay.on_message(msg)
        if self.history.add(msg):
            self.hub.publish("history", self.history.state())
        if self.transcript.on:
            who = self.speaker_tag(msg)      # 字幕檔只寫現在還算數的說話者（重新分辨之前標的不寫）
            self.transcript.add(dict(msg, speaker=who[0], voice=who[1]) if who else {k: v for k, v in msg.items() if k != "speaker"})
            self.hub.publish("record", self.record_state())
        return msg

    def _drop(self, msg_id: int) -> None:
        with self._lock:
            for old in list(self.messages):
                if old["id"] == msg_id:
                    self.messages.remove(old)
        self.hub.publish("message_removed", {"id": msg_id})
        self.overlay.on_removed(msg_id)
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
               place: str = "", live: dict | None = None, speaker: int | None = None) -> int | None:
        """via：screen（螢幕文字）/ heard（對方的聲音）/ spoken（我說的話）/ typed（我打的字）。
        unsure：辨識的時候沒什麼把握（介面會標「可能聽錯／看錯」，可以改原文重翻）。
        hear：語音辨識花了幾秒。place：螢幕上哪一個區域讀到的（開著不只一個區域時才有）。
        live：這一句還在說的時候已經先顯示出來的那一則（即時字幕）；整句接手同一則，不另外多一則。
        speaker：第幾個說話者（有開「分辨說話者」而且分得出來才有）。回傳這則訊息的編號。"""
        text = text.strip()[:MAX_TEXT]
        if not text:
            if live:
                self._drop(live["id"])
            return None
        lang = lang or detect_language(text) or "en"
        who = "them" if via in ("screen", "heard") else "me"
        msg = {"id": next(self._ids), "who": who, "via": via, "original": text, "lang": lang,
               "translated": "", "pron": "", "back": "", "copied": False, "pending": True, "error": "",
               "unsure": bool(unsure) and via != "typed", "time": time.strftime("%H:%M"), "at": time.time(),
               "hear": round(float(hear), 1), "took": 0.0, "place": str(place)[:30]}
        if speaker:
            msg.update(speaker=int(speaker), speaker_run=self._speaker_run, voice=self._speaker_names.get(int(speaker), ""))
        if live:      # 同一則、同一個時間，先翻出來的半句留在畫面上，等整句翻好再換掉
            msg.update(id=live["id"], time=live["time"], at=live["at"], partial=live.get("partial", ""))
        if via != "screen":      # 螢幕上讀到的要翻完才知道值不值得顯示，其他的先把原文顯示出來
            self._post(msg)
        self.jobs.put(msg)
        return msg["id"]

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
            return
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
                return
            msg = dict(msg, translated=out, target=target, pending=False, error="", why="", back="",
                       lost=numbers_lost(msg["original"], out, target, msg["lang"]),      # 原文有、譯文找不到的數字：畫面會提醒核對
                       pron=self._pronunciation(out, target) if msg["who"] == "me" else "")
            self._count(msg)
            if msg["who"] == "me" and target != self.cfg["my_lang"] and not msg.get("copied"):
                if msg["via"] == "typed" or self.cfg["copy_spoken"]:
                    msg["copied"] = oswin.copy_text(out)
            self._post(msg)
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

    def _translate(self, msg: dict, target: str) -> str:
        """翻一則。翻的時候譯文一邊出來一邊送給畫面（即時），最後回傳的才是整理過的整句。"""
        import inspect

        fn = self.translator.translate
        if "on_part" not in inspect.signature(fn).parameters:
            return fn(msg["original"], target, msg["lang"], self._context_for(msg))
        sent, shown = [0.0], msg.get("partial") or ""

        def part(text: str) -> None:
            now = time.monotonic()
            if now - sent[0] < PARTIAL_EVERY or not text.strip() or shown.startswith(text):
                return      # 畫面上已經有先翻的半句，新的還跟它開頭一樣：不用換，字才不會縮回去又長出來
            sent[0] = now
            self.hub.publish("partial", {"id": msg["id"], "text": text})
            self.overlay.on_partial(msg, text)

        return fn(msg["original"], target, msg["lang"], self._context_for(msg), on_part=part)

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
        if msg["id"] in self._counted:
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
        if not self.lessons.add(msg["original"], dst, msg["lang"], msg["target"]):
            return False
        self.translator.clear_cache()
        self.hub.publish("lessons", self.lessons.list())
        msg = dict(msg, translated=dst, error="", taught=True, back="",
                   pron=self._pronunciation(dst, msg["target"]) if msg["who"] == "me" else "")
        if msg["who"] == "me" and msg["target"] != self.cfg["my_lang"]:
            msg["copied"] = oswin.copy_text(dst)
        self._post(msg)
        self._want_back(msg)
        return True

    def lesson_remove(self, src: str, target: str) -> None:
        self.lessons.remove(src, target)
        self.translator.clear_cache()
        self.hub.publish("lessons", self.lessons.list())

    def glossary_add(self, src: str, dst: str) -> bool:
        ok = self.glossary.add(src, dst)
        if ok:
            self.translator.clear_cache()
            self.hub.publish("glossary", self.glossary.list())
        return ok

    def glossary_remove(self, src: str) -> None:
        self.glossary.remove(src)
        self.translator.clear_cache()
        self.hub.publish("glossary", self.glossary.list())

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
                                        "config": self._settings_now()}, ensure_ascii=False, indent=2), encoding="utf-8")
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
                        "on": bool(item.get("on", True)), "app": app, "anchor": anchor if app else None})
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
        changed = self._apply_settings({k: v for k, v in data["config"].items() if k in DEFAULTS and k not in SETTINGS_SKIP})
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

    def _note(self, key: str) -> None:
        """按了快速鍵之後的短提示：顯示在浮動留言上（人在遊戲裡），主視窗也跳一則。"""
        table = HOTKEY_NOTES.get(self.cfg["ui_lang"], HOTKEY_NOTES["en"])
        self.overlay.note(table[key])
        self.hub.publish("notice", {"code": "hotkey_note", "text": table[key]})

    def _hotkey(self, name: str) -> None:
        """快速鍵被按下（在快速鍵那條執行緒上）。只做很快的事，慢的另外開執行緒。"""
        if name == "quick":
            self.quick_input()      # 要在收到快速鍵的這條執行緒上開，Windows 才會讓輸入框跑到最前面
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

    def clear_messages(self) -> None:
        with self._lock:
            self.messages.clear()
            self._live.clear()
        self.hub.publish("cleared", {})
        self.overlay.clear()

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
        if "term_packs" in changes:
            picked = changes["term_packs"] if isinstance(changes["term_packs"], list) else []
            changes["term_packs"] = [name for name in PACKS if name in picked]
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
        if changed("game_terms"):
            self.translator.clear_cache()
        if changed("term_packs"):
            self._terms_changed()
        if changed("on_top") and self.window:
            self.window.set_on_top(after["on_top"])
        if changed("overlay", "overlay_form", "overlay_monitor", *OVERLAY_LOOK, "ui_lang", "my_lang", "their_lang"):
            self.overlay.apply()
            if changed("overlay"):
                self.hub.publish("overlay", self.overlay.state())
        if changed("their_auto"):
            self._their_guess = ""
        if changed("hotkeys"):
            self.hotkeys.set(after["hotkeys"])
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
        if self.cfg["their_auto"] and their != "ko":
            # 自動偵測：中文和日文用同一個文字辨識模型，兩種都收（韓文是另一個模型，要等聲音判斷出韓文才會換過去）
            langs |= {"zh", "ja"} - {my}
        if self.cfg["also_english"] and my != "en":
            langs.add("en")
        return langs

    def _load_ocr(self):
        from .core.ocr import OcrEngine

        family = LANGS[self.cfg["their_lang"]]["family"]
        if self.ocr is None or self.ocr.family != family:
            self.ocr = OcrEngine(self.store / "models" / "ocr", scale=float(self.cfg["ocr_scale"]), family=family)
        return self.ocr

    def _regions_on(self) -> list[dict]:
        """開著的聊天區。"""
        return [dict(r) for r in self.cfg["regions"] if r.get("on")]

    def _on_screen_line(self, text: str, lang: str, score: float = 1.0, region: dict | None = None) -> None:
        many = len(self._regions_on()) > 1      # 只有一個區域時不用標是哪裡讀到的
        self.submit("screen", text, lang, unsure=score < 0.8, place=(region or {}).get("name", "") if many else "")

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
                    on_stats=lambda stats: self.hub.publish("screen_stats", stats), on_black=self._screen_black)
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
            from .core.watcher import SeenLines, join_wrapped, text_edge

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
            # 自動偵測：四種語言都聽，目前判斷到的排第一個
            return list(dict.fromkeys([their, *VOICE_FAMILIES])) if self.cfg["their_auto"] else [their]
        return list(dict.fromkeys([my, their]))

    def _their_language_heard(self, family: str, text: str) -> None:
        """
        自動偵測：對方這句話聽起來是 family 這個語言。跟目前以為的不一樣，而且夠確定
        （句子夠長，或是連續兩句都是它），就把「對方用的語言」換過去。
        對方說的剛好是我的語言時不換：那句話本來就看得懂，換了反而我說的話沒有東西可以翻。
        """
        my, their = LANGS[self.cfg["my_lang"]]["family"], LANGS[self.cfg["their_lang"]]["family"]
        if family in (their, my) or family not in VOICE_FAMILIES:
            self._their_guess = ""
            return
        letters = len(re.sub(r"[\W_]+", "", text))
        solid = letters >= (16 if family == "en" else 6)
        if not solid and self._their_guess != family:
            self._their_guess = family
            return
        self._their_guess = ""
        code = family if family in LANGS else ("zh-CN" if self.cfg["ui_lang"] == "zh-CN" else "zh-TW")
        before = self.cfg.snapshot()
        self.cfg.set(their_lang=code)
        self.log(f"auto-detect: the other person now speaks {code} (was {before['their_lang']})")
        self.hub.publish("config", self.cfg.snapshot())
        self.notice("their_switched", lang=code)
        self.overlay.apply()
        threading.Thread(target=self._their_language_switched, daemon=True).start()

    def _their_language_switched(self) -> None:
        with self._lock:
            pipe = self.pipelines.get("listen")
            if pipe:
                pipe.langs = self._voice_langs("listen")      # 不用重開收音，換個順序就好
            if self.watcher:
                was_on = self.sources["screen"]["on"]
                self._stop_watcher()
                if was_on:
                    self._start_screen()
            if self.sources["mic"]["on"]:
                self._restart_voice("mic")

    def _start_voice(self, key: str, again: bool = False) -> None:
        """again：斷線之後自動重新連接的那幾次（失敗了不顯示錯誤，接著等下一次）。"""
        kind = VOICE_KIND[key]
        self._voice_open.pop(key, None)
        self._source(key, busy=True, error="")
        try:
            from .core.speech import Recognizer, SpeechPipeline

            if self.recognizer is None:
                model_dir = self.store / "models" / "whisper"
                if not (model_dir / "model.bin").exists():
                    raise RuntimeError("the speech model is missing")
                device, compute = self._speech_engine()
                self.recognizer = Recognizer(model_dir, threads=int(self.cfg["whisper_threads"]), device=device,
                                             compute_type=compute, on_fallback=self._speech_gpu_failed)
            device = self.cfg["speaker_device"] if key == "listen" else self.cfg["mic_device"]
            capture = None
            if key == "listen" and self.cfg["listen_apps"] and not self._apps_audio_off and hasattr(self.audio, "capture_apps"):
                capture = self.audio.capture_apps(list(self.cfg["listen_apps"]), on_error=self._apps_audio_error,
                                                  on_open=self._voice_opened)
            self._source(key, apps=bool(capture))      # 介面要知道現在是「只聽指定程式」還是整台電腦
            if capture is None:
                capture = self.audio.capture(kind, device, on_error=self._voice_error, on_open=self._voice_opened)
            self.recognizer.beam = BEAMS[self.cfg["speech_mode"]]
            if key == "listen":
                self.speakers_load()      # 有開「分辨說話者」的話，模型在背景準備（還沒好之前字幕照出，只是不標是誰）
            pipe = SpeechPipeline(capture, self.recognizer, self._voice_langs(key),
                                  on_text=self._on_speech, on_state=self._on_speaking,
                                  end_silence=float(self.cfg["end_silence"]), on_level=self._on_level,
                                  threshold=VAD_THRESHOLD[int(self.cfg["voice_sensitivity"])],
                                  gate=self._mic_gate if key == "mic" else None,
                                  # 即時字幕只給「對方說的話」：自己說的反正要等整句才複製，先辨識只會拖慢對方那一邊
                                  on_partial=self._on_live if key == "listen" else None,
                                  live=lambda: bool(self.cfg["live_captions"]), busy=self._live_wait,
                                  # 分辨說話者也只給「對方說的話」
                                  voices=self._voices_now if key == "listen" else None)
            if key == "listen" and self.cfg["their_auto"]:
                pipe.stay = 1.5
            old = self.pipelines.pop(key, None)
            if old:
                old.stop()
            pipe.start()
            self.pipelines[key] = pipe
            self._source(key, on=True, busy=False, quiet="")
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
        if silent_for >= 6 and not quiet:
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
                "people": [dict(p, name=self._speaker_names.get(p["id"], ""), known=remember and bool(self._speaker_names.get(p["id"])))
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

    def speakers_forget(self) -> dict:
        """全部重新分辨：忘掉目前分出來的人和名字（換了一群人，或分錯了）。之前的句子不再標是誰。
        以前記住的人（存在 voices.json 的）不會被忘掉：照存檔的樣子放回來，要忘掉某個人是把他的名字清空。"""
        if self.voices:
            self.voices.speakers.forget()
        self._speaker_names.clear()
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
            self._speaker_names[self.voices.speakers.seed(person["voice"], person["weight"])] = person["name"]

    def _voices_save(self) -> None:
        """把現在取過名字的人存起來（整份換掉）。沒開「記住」、或聲紋模型還沒準備好（還不知道以前記了誰）就不動那個檔。"""
        if not (self.voices and self.cfg["remember_speakers"]):
            return
        people = []
        for n, name in sorted(self._speaker_names.items()):
            got = self.voices.speakers.voice_of(n)
            if got and name:
                people.append({"name": name, "voice": got[0], "weight": got[1]})
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
                   speaker: int | None = None) -> None:
        if lang == "error":
            self._source(SOURCE_OF[kind], error=text)
            self._end_live(kind)
            return
        if self.cfg["drop_echo"] and self._is_echo(kind, text, lang):
            self._end_live(kind)
            return
        if kind == "loopback" and self.cfg["their_auto"]:
            self._their_language_heard(lang, text)
        text = self._voice_script(kind, text, lang)
        heard = text
        msg_id = self.submit("heard" if kind == "loopback" else "spoken", text, lang, unsure=unsure, hear=seconds,
                             live=self._take_live(kind), speaker=speaker if kind == "loopback" else None)
        if speaker:
            self.hub.publish("speakers", self.speakers_state())      # 多了一個人，或某個人又多說一句
            if self._speaker_names.get(int(speaker)) and time.time() - self._voices_saved > VOICES_SAVE_EVERY:
                self._voices_save()      # 記住的人：聲紋跟著今天的聲音調整過了，隔一陣子存一次
        self._recent_voice.append({"kind": kind, "text": heard, "lang": lang, "at": time.time(), "id": msg_id})

    def _voice_script(self, kind: str, text: str, lang: str | None) -> str:
        """語音辨識給的中文有時是繁體有時是簡體：照說話的人那一邊選的語言統一。"""
        if lang != "zh":
            return text
        from .core.translate import to_script

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
        if self._quick is not None and self._quick.poll() is None:
            self._quick.kill()
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
        self.overlay.stop()
        self._stop_watcher()
        for key in list(self.pipelines):
            self._stop_voice(key)
        if self.engine:
            self.engine.stop()
        self.cfg.save()
