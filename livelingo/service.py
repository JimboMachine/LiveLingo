"""
程式的主體：管理翻譯引擎、三種來源（讀螢幕、聽對方說話、聽我說話）、詞彙表和設定，
把發生的事情用事件送給介面。介面只是顯示和按按鈕，所有工作都在這裡做。
"""

import itertools
import json
import os
import queue
import subprocess
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from . import __version__, assets, gpu, oswin, updater
from .config import EDITABLE, UI_LANGS, Config, Glossary
from .core.engine import Engine
from .core.romanize import romanize
from .core.translate import LANGS, Translator, detect_language
from .paths import self_command
from .terms import TermPack

VOICE_KIND = {"listen": "loopback", "mic": "mic"}
SOURCE_OF = {"loopback": "listen", "mic": "mic"}


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


class Service:
    def __init__(self, data: Path, hub: EventHub | None = None, audio=None):
        self.data = data
        self.hub = hub or EventHub()
        self.cfg = Config(data)
        self.glossary = Glossary(data)
        self.terms = TermPack()
        self.audio = audio or RealAudio()
        self.gpu = {"name": None, "vram_mb": 0, "tier": "cpu"}
        self.mode = {"model": "4b", "engine": "cpu", "quality": "standard"}
        self.fallback = False          # 高品質開不起來，自動退回標準
        self.engine: Engine | None = None
        self.translator = Translator("http://127.0.0.1:8177", glossary=self.glossary.as_dict,
                                     tone=self.cfg["tone"], hints=self._hints)
        self.phase = "starting"        # starting / updating / need_download / downloading / loading / ready / error
        self.detail: dict = {}
        self.sources = {k: {"on": False, "busy": False, "error": "", "device": "", "speaking": False}
                        for k in ("screen", "listen", "mic")}
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
        self.closing = threading.Event()
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._download_cancel = threading.Event()
        self._selecting = False
        self.on_quit = lambda: None    # 請視窗關閉、讓程式正常結束（介面按離開）
        self.exit_now = lambda: self.on_quit()   # 立刻結束整個程式（更新時用；__main__ 會換成真的）

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
            "fallback": self.fallback,
            "sources": self.sources,
            "messages": list(self.messages),
            "glossary": self.glossary.list(),
            "langs": {k: v["family"] for k, v in LANGS.items()},
            "ui_langs": list(UI_LANGS),
            "can_window": bool(self.window or self.can_window),
            "view": self.view,
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

    # ------------------------------------------------------------------ 啟動

    def start(self) -> None:
        threading.Thread(target=self._translate_worker, daemon=True, name="translate").start()
        threading.Thread(target=self._boot, daemon=True, name="boot").start()

    def _boot(self) -> None:
        try:
            if self._update_if_needed():
                return
            self.gpu = gpu.detect()
            self._prepare()
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self.set_phase("error", message=str(ex))

    def _update_if_needed(self) -> bool:
        """有新版本就下載安裝並結束程式，回傳 True。"""
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
        try:
            self.set_phase("updating", step="downloading", version=info["version"], percent=0)
            last = [0.0]

            def progress(done, total):
                if time.time() - last[0] > 0.4 and total:
                    last[0] = time.time()
                    self.set_phase("updating", step="downloading", version=info["version"],
                                   percent=int(done * 100 / total))

            path = updater.download(info, self.data / "update", progress)
            self.set_phase("updating", step="installing", version=info["version"])
            attempts.record(info["version"])
            updater.launch_installer(path)
        except Exception as ex:  # noqa: BLE001
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

    def _pronunciation(self, text: str, target: str) -> str:
        if self.cfg["show_pronunciation"] and LANGS[target]["family"] == "ko":
            return romanize(text)
        return ""

    def _post(self, msg: dict) -> dict:
        with self._lock:
            for i, old in enumerate(self.messages):
                if old["id"] == msg["id"]:
                    self.messages[i] = msg
                    break
            else:
                self.messages.append(msg)
        self.hub.publish("message", msg)
        return msg

    def _drop(self, msg_id: int) -> None:
        with self._lock:
            for old in list(self.messages):
                if old["id"] == msg_id:
                    self.messages.remove(old)
        self.hub.publish("message_removed", {"id": msg_id})

    def submit(self, via: str, text: str, lang: str | None = None) -> None:
        """via：screen（螢幕文字）/ heard（對方的聲音）/ spoken（我說的話）/ typed（我打的字）。"""
        text = text.strip()
        if not text:
            return
        lang = lang or detect_language(text) or "en"
        who = "them" if via in ("screen", "heard") else "me"
        msg = {"id": next(self._ids), "who": who, "via": via, "original": text, "lang": lang,
               "translated": "", "pron": "", "copied": False, "pending": True, "error": "",
               "time": time.strftime("%H:%M")}
        if via != "screen":      # 螢幕上讀到的要翻完才知道值不值得顯示，其他的先把原文顯示出來
            self._post(msg)
        self.jobs.put(msg)

    def _target_for(self, msg: dict) -> str:
        my, their = self.cfg["my_lang"], self.cfg["their_lang"]
        if msg["who"] == "them":
            return my
        # 我打的字、我說的話：是我的語言就翻成對方的語言，否則翻回我的語言
        return their if msg["lang"] == LANGS[my]["family"] else my

    def _translate_worker(self) -> None:
        while not self.closing.is_set():
            try:
                msg = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                target = self._target_for(msg)
                out = self.translator.translate(msg["original"], target, msg["lang"])
                if msg["via"] == "screen" and (not out or out.strip() == msg["original"].strip()):
                    continue
                msg = dict(msg, translated=out, target=target, pending=False, error="",
                           pron=self._pronunciation(out, target) if msg["who"] == "me" else "")
                if msg["who"] == "me" and target != self.cfg["my_lang"] and not msg.get("copied"):
                    if msg["via"] == "typed" or self.cfg["copy_spoken"]:
                        msg["copied"] = oswin.copy_text(out)
                self._post(msg)
            except Exception as ex:  # noqa: BLE001
                self.log(traceback.format_exc())
                self._post(dict(msg, pending=False, error=str(ex)))

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

    def copy(self, msg_id: int) -> bool:
        with self._lock:
            msg = next((m for m in self.messages if m["id"] == msg_id), None)
        return bool(msg and msg.get("translated") and oswin.copy_text(msg["translated"]))

    def clear_messages(self) -> None:
        with self._lock:
            self.messages.clear()
        self.hub.publish("cleared", {})

    # ------------------------------------------------------------------ 設定

    def update_config(self, changes: dict) -> None:
        changes = {k: v for k, v in changes.items() if k in EDITABLE}
        if changes.get("ui_lang") not in (None, *UI_LANGS):
            changes.pop("ui_lang")
        for key in ("my_lang", "their_lang"):
            if key in changes and changes[key] not in LANGS:
                changes.pop(key)
        if changes.get("tone") not in (None, "casual", "polite"):
            changes.pop("tone")
        if changes.get("quality") not in (None, "auto", "standard", "high"):
            changes.pop("quality")
        before = self.cfg.snapshot()
        # 兩邊選到同一個語言時，自動對調
        my, their = changes.get("my_lang", before["my_lang"]), changes.get("their_lang", before["their_lang"])
        if my == their:
            if "my_lang" in changes and "their_lang" not in changes:
                changes["their_lang"] = before["my_lang"]
            else:
                changes["my_lang"] = before["their_lang"]
        self.cfg.set(**changes)
        after = self.cfg.snapshot()
        self.hub.publish("config", after)

        def changed(*keys):
            return any(before[k] != after[k] for k in keys)

        if changed("tone"):
            self.translator.tone = after["tone"]
            self.translator.clear_cache()
        if changed("game_terms"):
            self.translator.clear_cache()
        if changed("on_top") and self.window:
            self.window.set_on_top(after["on_top"])
        if changed("my_lang", "their_lang", "also_english"):
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
        langs = {LANGS[self.cfg["their_lang"]]["family"]}
        if self.cfg["also_english"] and LANGS[self.cfg["my_lang"]]["family"] != "en":
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
                    ocr, on_message=lambda text, lang: self.submit("screen", text, lang),
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
                self.cfg.set(region=region)
                self.hub.publish("config", self.cfg.snapshot())
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
        return [their] if key == "listen" else [my, their]

    def _start_voice(self, key: str) -> None:
        kind = VOICE_KIND[key]
        self._source(key, busy=True, error="")
        try:
            from .core.speech import Recognizer, SpeechPipeline

            if self.recognizer is None:
                model_dir = self.data / "models" / "whisper"
                if not (model_dir / "model.bin").exists():
                    raise RuntimeError("the speech model is missing")
                self.recognizer = Recognizer(model_dir, threads=int(self.cfg["whisper_threads"]))
            device = self.cfg["speaker_device"] if key == "listen" else self.cfg["mic_device"]
            capture = self.audio.capture(
                kind, device, on_error=self._voice_error,
                on_open=lambda k, name: self._source(SOURCE_OF[k], device=name))
            pipe = SpeechPipeline(capture, self.recognizer, self._voice_langs(key),
                                  on_text=self._on_speech, on_state=self._on_speaking,
                                  end_silence=float(self.cfg["end_silence"]))
            old = self.pipelines.pop(key, None)
            if old:
                old.stop()
            pipe.start()
            self.pipelines[key] = pipe
            self._source(key, on=True, busy=False)
        except Exception as ex:  # noqa: BLE001
            self.log(traceback.format_exc())
            self._source(key, on=False, busy=False, error=str(ex))

    def _stop_voice(self, key: str) -> None:
        pipe = self.pipelines.pop(key, None)
        if pipe:
            pipe.stop()

    def _restart_voice(self, key: str) -> None:
        self._stop_voice(key)
        threading.Thread(target=self._start_voice, args=(key,), daemon=True).start()

    def _on_speech(self, kind: str, text: str, lang: str, seconds: float = 0.0) -> None:
        if lang == "error":
            self._source(SOURCE_OF[kind], error=text)
            return
        if lang == "zh" and "zh-TW" in (self.cfg["my_lang"], self.cfg["their_lang"]):
            from .core.translate import _to_traditional

            text = _to_traditional.convert(text)      # 語音辨識有時會給簡體字
        self.submit("heard" if kind == "loopback" else "spoken", text, lang)

    def _on_speaking(self, kind: str, speaking: bool) -> None:
        self._source(SOURCE_OF[kind], speaking=speaking)

    def _voice_error(self, kind: str, ex: Exception) -> None:
        key = SOURCE_OF[kind]
        self.log(f"{kind} capture failed: {ex!r}")
        self._stop_voice(key)
        self._source(key, on=False, busy=False, speaking=False, error=str(ex))

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
            self._source(key, on=False, speaking=False, error="")

    # ------------------------------------------------------------------ 聲音裝置

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
        self.closing.set()
        self._download_cancel.set()
        if self.meters:
            self.meters.stop()
        self._stop_watcher()
        for key in list(self.pipelines):
            self._stop_voice(key)
        if self.engine:
            self.engine.stop()
        self.cfg.save()
