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
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from . import __version__, apps, assets, chatpaint, gpu, oswin, updater
from .config import ACCENTS, DEFAULTS, EDITABLE, OVERLAY_LOOK, THEMES, UI_LANGS, Config, Glossary, Lessons
from .core.engine import Engine
from .core.romanize import romanize
from .core.translate import LANGS, Translator, detect_language, similarity
from .overlay import Overlay
from .paths import self_command
from .terms import PACKS, TermPack
from .transcript import Transcript
from .tray import Tray

VOICE_KIND = {"listen": "loopback", "mic": "mic"}
SOURCE_OF = {"loopback": "listen", "mic": "mic"}
VOICE_FAMILIES = ("ko", "zh", "en", "ja")      # 語音辨識認得、我們也翻得了的語言


# 「靈敏度」1 到 5 各是多少的語音偵測門檻（越低越容易把聲音當成說話）
VAD_THRESHOLD = {1: 0.7, 2: 0.6, 3: 0.5, 4: 0.4, 5: 0.3}
BEAMS = {"fast": 1, "accurate": 5}      # 語音辨識「比較快／比較準」各用多寬的搜尋
LIBRARY_KIND = "livelingo-library"      # 匯出檔裡用來認「這是 LiveLingo 的詞彙檔」


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
        self.sources = {k: {"on": False, "busy": False, "error": "", "device": "", "speaking": False, "quiet": "",
                            "apps": False}
                        for k in ("screen", "listen", "mic")}
        self._vu = {"listen": 0.0, "mic": 0.0}
        self._vu_sent = 0.0
        self._finding_mic = False
        self._find_cancel = threading.Event()
        self._update_choices: queue.Queue = queue.Queue()      # 使用者對「有新版本」的回答
        self._update_stop = threading.Event()
        self.messages: deque[dict] = deque(maxlen=200)
        self.jobs: queue.Queue = queue.Queue()
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
        self.tray = Tray(self)
        self._their_guess = ""      # 自動偵測：上一句聽起來像哪個別的語言（還不夠確定）
        self._recent_voice: deque[dict] = deque(maxlen=12)      # 最近聽到的句子（兩邊都記），用來認出麥克風收到的喇叭聲音
        self.delays: deque[tuple[float, float]] = deque(maxlen=10)      # 最近幾句語音：（辨識秒數, 翻譯秒數）
        # 語音辨識用顯示卡：state = off（沒選）/ need（要先下載）/ downloading / ready / failed（試過不行，已改用處理器）
        self.speech_gpu = {"state": "off", "percent": 0, "error": "", "mb": assets.CUDNN_MB, "active": False}
        self._speech_gpu_cancel = threading.Event()
        self._apps_audio_off = False      # 「只聽指定程式」這次試過不行，先改聽整台電腦（換選擇或重開程式會再試）
        self._lock = threading.RLock()
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

    def state(self) -> dict:
        return {
            "version": __version__,
            "config": self.cfg.snapshot(),
            "phase": self.phase,
            "detail": self.detail,
            "gpu": self.gpu,
            "mode": self.mode,
            "delay": self.delay(),
            "speech_gpu": self.speech_gpu_state(),
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
        chatpaint.preview(self.cfg.snapshot()).save(out, "PNG")
        return out.getvalue()

    def stored(self, what: str, ok: bool = True) -> None:
        """不是在設定畫面上直接改的東西（框選的範圍、浮動留言的位置、字幕資料夾）存好了：讓介面顯示「已儲存」。"""
        if ok:
            self.hub.publish("saved", {"what": what})

    # ------------------------------------------------------------------ 啟動

    def start(self) -> None:
        threading.Thread(target=self._translate_worker, daemon=True, name="translate").start()
        threading.Thread(target=self._boot, daemon=True, name="boot").start()

    def _boot(self) -> None:
        try:
            if self._update_if_needed():
                return
            self.overlay.apply()
            self.gpu = gpu.detect()
            self._speech_engine()      # 先把「語音辨識用顯示卡」現在的狀態算好，介面才知道要不要顯示下載
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
        self.set_phase("updating", step="checking")
        try:
            info = updater.check()
        except Exception as ex:  # noqa: BLE001  沒有網路：照常使用
            self.log(f"update check failed: {ex!r}")
            return False
        if not info:
            return False
        attempts = updater.Attempts(self.data)
        if attempts.recently_tried(info["version"]):
            self.log(f"update to {info['version']} was just tried; not retrying yet")
            self.notice("update_failed", version=info["version"])
            return False
        self._update_stop.clear()
        self.set_phase("updating", step="ask", version=info["version"])
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

    def _prepare(self) -> None:
        """決定模式、確認檔案都在、啟動翻譯引擎。"""
        self.mode = gpu.choose_mode("standard" if self.fallback else self.cfg["quality"], self.gpu)
        if self.fallback:
            self.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        need = assets.missing(self.data, self.mode)
        if need:
            self.set_phase("need_download", items=need, total_mb=sum(i["mb"] for i in need))
            return
        self._start_engine()

    def start_download(self) -> None:
        if self.phase != "need_download":
            return
        items = self.detail.get("items") or assets.missing(self.data, self.mode)
        self._download_cancel.clear()
        self.set_phase("downloading", key=items[0]["key"], done_mb=0, total_mb=sum(i["mb"] for i in items),
                       index=0, count=len(items))

        def work():
            try:
                assets.download_all(self.data, items, lambda p: self.set_phase("downloading", **p),
                                    self._download_cancel.is_set)
                self._start_engine()
            except Exception as ex:  # noqa: BLE001
                self.log(traceback.format_exc())
                self.set_phase("need_download", items=assets.missing(self.data, self.mode),
                               total_mb=sum(i["mb"] for i in assets.missing(self.data, self.mode)),
                               error=str(ex))

        threading.Thread(target=work, daemon=True, name="download").start()

    def _start_engine(self) -> None:
        self.set_phase("loading", model=self.mode["model"], engine=self.mode["engine"])
        if self.engine:
            self.engine.stop()
        gpu_mode = self.mode["engine"] == "cuda"
        self.engine = Engine(
            exe=assets.find_server(assets.engine_dir(self.data, self.mode["engine"])),
            model=assets.model_path(self.data, self.mode["model"]),
            log_path=self.data / "engine.log", threads=int(self.cfg["threads"]),
            gpu_layers=99 if gpu_mode else 0)
        self.translator.url = self.engine.url
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

    def _resume_sources(self) -> None:
        """上次關掉程式時開著的來源，這次自動再打開。"""
        if not self.cfg["setup_done"]:
            return
        want = self.cfg["sources"]
        if want.get("screen") and self.cfg["region"]:
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
        with self._lock:
            for i, old in enumerate(self.messages):
                if old["id"] == msg["id"]:
                    self.messages[i] = msg
                    break
            else:
                self.messages.append(msg)
        self.hub.publish("message", msg)
        self.overlay.on_message(msg)
        if self.transcript.on:
            self.transcript.add(msg)
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

    def submit(self, via: str, text: str, lang: str | None = None, unsure: bool = False, hear: float = 0.0) -> int | None:
        """via：screen（螢幕文字）/ heard（對方的聲音）/ spoken（我說的話）/ typed（我打的字）。
        unsure：辨識的時候沒什麼把握（介面會標「可能聽錯／看錯」，可以改原文重翻）。
        hear：語音辨識花了幾秒。回傳這則訊息的編號。"""
        text = text.strip()
        if not text:
            return None
        lang = lang or detect_language(text) or "en"
        who = "them" if via in ("screen", "heard") else "me"
        msg = {"id": next(self._ids), "who": who, "via": via, "original": text, "lang": lang,
               "translated": "", "pron": "", "back": "", "copied": False, "pending": True, "error": "",
               "unsure": bool(unsure) and via != "typed", "time": time.strftime("%H:%M"), "at": time.time(),
               "hear": round(float(hear), 1), "took": 0.0}
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
                msg = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if "back_of" in msg:
                self._back_translate(msg["back_of"])
                continue
            try:
                target = self._target_for(msg)
                started = time.time()
                out = self.translator.translate(msg["original"], target, msg["lang"], self._context_for(msg))
                spent = time.time() - started
                if msg["via"] in ("heard", "spoken") and not msg.get("edited") and not msg.get("took"):
                    self.delays.append((float(msg.get("hear") or 0.0), spent))      # 只算第一次翻的，重翻的不算
                    self.hub.publish("delay", self.delay())
                    msg = dict(msg, took=round(float(msg.get("hear") or 0.0) + spent, 1))
                if msg["via"] == "screen" and (not out or out.strip() == msg["original"].strip()):
                    continue
                msg = dict(msg, translated=out, target=target, pending=False, error="", back="",
                           pron=self._pronunciation(out, target) if msg["who"] == "me" else "")
                if msg["who"] == "me" and target != self.cfg["my_lang"] and not msg.get("copied"):
                    if msg["via"] == "typed" or self.cfg["copy_spoken"]:
                        msg["copied"] = oswin.copy_text(out)
                self._post(msg)
                self._want_back(msg)
            except Exception as ex:  # noqa: BLE001
                self.log(traceback.format_exc())
                self._post(dict(msg, pending=False, error=str(ex)))

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
            self.jobs.put(msg)
        return True

    def edit(self, msg_id: int, text: str) -> bool:
        """「聽錯了／看錯了」：把辨識出來的原文改成對的，重新翻這一則。"""
        text = text.strip()[:500]
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if not msg or not text or msg.get("pending") or text == msg["original"]:
            return False
        msg = dict(msg, original=text, lang=detect_language(text) or msg["lang"], unsure=False, edited=True,
                   pending=True, copied=False, taught=False, back="", error="")
        self._post(msg)
        self.jobs.put(msg)
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

    def clear_messages(self) -> None:
        with self._lock:
            self.messages.clear()
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
        if "term_packs" in changes:
            picked = changes["term_packs"] if isinstance(changes["term_packs"], list) else []
            changes["term_packs"] = [name for name in PACKS if name in picked]
        for key, allowed in (("overlay_family", chatpaint.FAMILIES), ("overlay_align", chatpaint.ALIGNS), ("overlay_grow", chatpaint.GROWS)):
            if key in changes and changes[key] not in allowed:
                changes.pop(key)
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
        if changed("overlay", *OVERLAY_LOOK, "ui_lang", "my_lang", "their_lang"):
            self.overlay.apply()
            if changed("overlay"):
                self.hub.publish("overlay", self.overlay.state())
        if changed("their_auto"):
            self._their_guess = ""
        if changed("listen_apps"):
            self._apps_audio_off = False      # 換了選擇：再試一次
            if self.sources["listen"]["on"]:
                self._restart_voice("listen")
        if changed("speech_device"):
            threading.Thread(target=self._speech_device_changed, daemon=True).start()
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
            self.ocr = OcrEngine(self.data / "models" / "ocr", scale=float(self.cfg["ocr_scale"]), family=family)
        return self.ocr

    def _start_screen(self) -> None:
        from .core.watcher import ScreenWatcher

        self._source("screen", busy=True, error="")
        try:
            ocr = self._load_ocr()
            if self.watcher is None:
                self.watcher = ScreenWatcher(
                    ocr, on_message=lambda text, lang, score=1.0: self.submit("screen", text, lang, unsure=score < 0.8),
                    on_status=self._screen_error,
                    interval=float(self.cfg["interval"]), source_langs=self._screen_langs(),
                    grab=getattr(self, "grab", None))
                self.watcher.set_region(self.cfg["region"])
                self.watcher.start()
            self.watcher.paused = False
            self._source("screen", on=True, busy=False)
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self._source("screen", on=False, busy=False, error=str(ex))

    def _screen_error(self, err: str) -> None:
        if self.watcher and self.watcher.failed:
            self.log(f"screen watcher stopped: {err}")
            self.watcher = None
            self._source("screen", on=False, busy=False, error=err)
        else:
            self._source("screen", error=err)

    def _stop_watcher(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    def select_region(self, then_enable: bool = True) -> None:
        """跳出框選畫面（另外開一個小程式），框好之後存起來。"""
        if self._selecting:
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
                                       timeout=300, stdin=subprocess.DEVNULL)
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
                ok = self.cfg.set(region=region)
                self.hub.publish("config", self.cfg.snapshot())
                self.stored("region", ok)
                if self.watcher:
                    self.watcher.set_region(region)
                if then_enable:
                    self.set_source("screen", True)
            elif region:
                self.notice("region_small")
            self.hub.publish("region_done", {"ok": bool(region)})

        threading.Thread(target=work, daemon=True, name="region").start()

    def ocr_test(self) -> dict:
        """設定頁的「試讀一次」：現在就讀一次框選的區域，回傳讀到的文字。"""
        region = self.cfg["region"]
        if not region:
            return {"ok": False, "error": "no_region"}
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
            lines = [ln.text for ln in message_lines(ocr.read(bgr))]
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

    def _start_voice(self, key: str) -> None:
        kind = VOICE_KIND[key]
        self._source(key, busy=True, error="")
        try:
            from .core.speech import Recognizer, SpeechPipeline

            if self.recognizer is None:
                model_dir = self.data / "models" / "whisper"
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
            pipe = SpeechPipeline(capture, self.recognizer, self._voice_langs(key),
                                  on_text=self._on_speech, on_state=self._on_speaking,
                                  end_silence=float(self.cfg["end_silence"]), on_level=self._on_level,
                                  threshold=VAD_THRESHOLD[int(self.cfg["voice_sensitivity"])],
                                  gate=self._mic_gate if key == "mic" else None)
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
            self._source(key, on=False, busy=False, error=describe(ex))

    def _stop_voice(self, key: str) -> None:
        pipe = self.pipelines.pop(key, None)
        if pipe:
            pipe.stop()
        self._vu[key] = 0.0
        self.hub.publish("vu", dict(self._vu))

    def _voice_opened(self, kind: str, name: str) -> None:
        self.log(f"{kind} capture opened: {name}")
        self._source(SOURCE_OF[kind], device=name)

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
        runtime = assets.cuda_runtime_dir(self.data)
        return [runtime, assets.cudnn_dir(self.data)] if runtime and assets.has_cudnn(self.data) else None

    def _speech_engine(self) -> tuple[str, str]:
        """語音辨識現在該用什麼跑：（"cuda", "float16"）或（"cpu", "int8"）。順便更新給介面看的狀態。"""
        if self.cfg["speech_device"] != "gpu" or self.gpu.get("tier", "cpu") == "cpu":
            self._speech_gpu(state="off", active=False, error="")
            return "cpu", "int8"
        if self.speech_gpu["state"] in ("failed", "downloading"):
            return "cpu", "int8"
        folders = self._speech_gpu_files()
        if not folders:
            if assets.has_cudnn(self.data):      # cuDNN 有了，缺的是顯示卡版翻譯引擎帶來的 CUDA 檔案：下載也沒用
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
                assets.get_cudnn(self.data, lambda done, total: self._speech_gpu(
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

    def _on_speech(self, kind: str, text: str, lang: str, seconds: float = 0.0, unsure: bool = False) -> None:
        if lang == "error":
            self._source(SOURCE_OF[kind], error=text)
            return
        if self.cfg["drop_echo"] and self._is_echo(kind, text, lang):
            return
        if kind == "loopback" and self.cfg["their_auto"]:
            self._their_language_heard(lang, text)
        if lang == "zh":
            from .core.translate import to_script

            # 語音辨識給的中文有時是繁體有時是簡體：照說話的人那一邊選的語言統一
            speaker, other = ((self.cfg["their_lang"], self.cfg["my_lang"]) if kind == "loopback"
                              else (self.cfg["my_lang"], self.cfg["their_lang"]))
            text = to_script(text, speaker if LANGS[speaker]["family"] == "zh" else other)
        heard = text
        msg_id = self.submit("heard" if kind == "loopback" else "spoken", text, lang, unsure=unsure, hear=seconds)
        self._recent_voice.append({"kind": kind, "text": heard, "lang": lang, "at": time.time(), "id": msg_id})

    def _on_speaking(self, kind: str, speaking: bool) -> None:
        self._source(SOURCE_OF[kind], speaking=speaking)

    def _voice_error(self, kind: str, ex: Exception) -> None:
        key = SOURCE_OF[kind]
        self.log(f"{kind} capture failed: {ex!r}")
        self._stop_voice(key)
        self._source(key, on=False, busy=False, speaking=False, quiet="", error=str(ex) or type(ex).__name__)

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
            if on and not self.cfg["region"]:
                self.select_region(then_enable=True)
            elif on:
                threading.Thread(target=self._start_screen, daemon=True).start()
            else:
                if self.watcher:
                    self.watcher.paused = True
                self._source("screen", on=False, error="")
        elif on:
            threading.Thread(target=self._start_voice, args=(key,), daemon=True).start()
        else:
            self._stop_voice(key)
            self._source(key, on=False, speaking=False, error="", quiet="")

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
        """main（主畫面）/ settings（設定）/ subtitle（字幕列）/ wizard（第一次設定）。"""
        if view not in ("main", "settings", "subtitle", "wizard"):
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
        if self.transcript.on:      # 記錄到一半就關程式：先存起來，不要弄丟
            try:
                self.transcript.stop(self.transcript_dir(), self.cfg["ui_lang"], bool(self.cfg["transcript_original"]))
            except OSError as ex:
                self.log(f"subtitle log could not be saved: {ex!r}")
        self.closing.set()
        self._download_cancel.set()
        if self.meters:
            self.meters.stop()
        self.overlay.stop()
        self._stop_watcher()
        for key in list(self.pipelines):
            self._stop_voice(key)
        if self.engine:
            self.engine.stop()
        self.cfg.save()
