"""
本地端即時翻譯小視窗
  1. 框選螢幕上的聊天區 → 對方傳來新訊息就自動辨識並翻譯成繁體中文
  2. 複製任何文字 → 自動翻譯
  3. 在下方輸入中文 → 翻成韓文並自動複製，貼上就能傳給對方
  4. 聽對方說話 → 把電腦播放的聲音辨識成文字，翻成繁體中文顯示出來
  5. 聽我說話 → 你對麥克風說中文就翻成韓文（附唸法），說韓文就翻成中文
所有辨識和翻譯都在這台電腦上完成，不會把內容傳到外面。
"""

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
# 語音辨識和其他元件各自帶了一份 OpenMP，Windows 上同時載入時要加這個才不會被擋下來
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(ROOT))

from core.engine import Engine  # noqa: E402
from core.romanize import romanize  # noqa: E402
from core.translate import LANGS, Translator, detect_language  # noqa: E402

CONFIG_PATH = ROOT / "config.json"
DEFAULTS = {
    "my_lang": "zh-TW",          # 我看得懂的語言（對方的訊息翻成這個）
    "friend_lang": "ko",         # 對方的語言（我打的字翻成這個）
    "also_english": False,       # 螢幕上的英文訊息也翻譯
    "tone": "casual",            # casual = 朋友語氣（韓文半語）；polite = 禮貌語氣
    "clipboard": True,           # 複製就翻譯
    "opacity": 0.92,
    "interval": 0.7,             # 每隔幾秒看一次螢幕
    "ocr_scale": 1.5,            # 辨識前先把畫面放大，小字比較準
    "threads": 0,                # 翻譯引擎用幾個 CPU 核心，0 = 自動
    "gpu_layers": 0,             # 有裝 GPU 版引擎才需要改
    "region": None,
    # ---- 語音 ----
    "listen_langs": None,        # 對方說的語言，None = 跟 friend_lang 一樣；例如 ["ko", "en"]
    "mic_langs": None,           # 你會說的語言，None = 中文和對方的語言都可以；只說中文可改成 ["zh"]
    "show_pronunciation": True,  # 韓文下面顯示唸法（羅馬拼音）
    "copy_spoken": True,         # 你用說的翻出來的句子也自動複製
    "end_silence": 0.6,          # 停頓多久算一句話說完（秒）
    "whisper_threads": 4,        # 語音辨識用幾個 CPU 核心
    "speaker_device": None,      # 要聽哪個播放裝置（名稱的一部分），None = 系統預設
    "mic_device": None,          # 要用哪支麥克風（名稱的一部分），None = 系統預設
    "geometry": "480x600+60+60",
}

BG, BG2, FG, DIM, ACCENT = "#1e1f22", "#2b2d31", "#f2f3f5", "#949ba4", "#5865f2"
IS_WIN = sys.platform == "win32"
FONT = "Microsoft JhengHei UI" if IS_WIN else "Noto Sans CJK TC"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    return cfg


def log_error(text: str) -> None:
    try:
        with open(ROOT / "app.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def virtual_screen() -> tuple[int, int, int, int]:
    """所有螢幕合起來的範圍 (left, top, width, height)。"""
    if IS_WIN:
        import ctypes

        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return 0, 0, 0, 0


class App:
    def __init__(self):
        self.cfg = load_config()
        if IS_WIN:
            try:  # 讓視窗座標和螢幕擷取的座標一致（高 DPI 螢幕需要）
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:  # noqa: BLE001
                pass
        self.root = tk.Tk()
        self.root.title("即時翻譯")
        self.root.configure(bg=BG)
        self.root.geometry(self.cfg["geometry"])
        self.root.minsize(340, 260)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", float(self.cfg["opacity"]))
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.ui_q: queue.Queue = queue.Queue()      # 背景工作 → 畫面
        self.jobs: queue.Queue = queue.Queue()      # 要翻譯的東西
        self.engine = Engine(ROOT, threads=int(self.cfg["threads"]), gpu_layers=int(self.cfg["gpu_layers"]))
        self.translator = Translator(self.engine.url, ROOT / "glossary.txt", tone=self.cfg["tone"])
        self.watcher = None
        self.recognizer = None
        self.pipelines: dict = {}                   # "loopback" / "mic" → 正在跑的語音管線
        self.voice_buttons: dict = {}
        self.last_clip = ""
        self.own_clip = ""
        self.closing = False

        self._build_ui()
        self._exclude_from_capture()
        self._minimize_console()
        threading.Thread(target=self._boot, daemon=True).start()
        threading.Thread(target=self._translate_worker, daemon=True).start()
        self.root.after(100, self._drain_ui)
        self.root.after(800, self._poll_clipboard)

    # ---------- 畫面 ----------

    def _button(self, parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd, bg=BG2, fg=FG, activebackground=ACCENT, activeforeground=FG,
                      relief="flat", bd=0, padx=8, pady=3, font=(FONT, 9), cursor="hand2")
        b.pack(side="left", padx=(0, 4))
        return b

    def _build_ui(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=8, pady=(8, 4))
        self._button(bar, "框選聊天區", self.select_region)
        self.pause_btn = self._button(bar, "暫停", self.toggle_pause)
        self._button(bar, "清除", self.clear)
        self.clip_var = tk.BooleanVar(value=bool(self.cfg["clipboard"]))
        tk.Checkbutton(bar, text="複製就翻譯", variable=self.clip_var, command=self._save_flags, bg=BG, fg=DIM,
                       selectcolor=BG2, activebackground=BG, activeforeground=FG, font=(FONT, 9)).pack(side="left")

        bar2 = tk.Frame(self.root, bg=BG)
        bar2.pack(fill="x", padx=8, pady=(0, 4))
        self.voice_buttons["loopback"] = self._button(bar2, "聽對方說話：關", lambda: self.toggle_voice("loopback"))
        self.voice_buttons["mic"] = self._button(bar2, "聽我說話：關", lambda: self.toggle_voice("mic"))
        self._button(bar2, "裝置", self.choose_devices)

        self.status = tk.Label(self.root, text="啟動中…", bg=BG, fg=DIM, anchor="w", justify="left", font=(FONT, 9))
        self.status.pack(fill="x", padx=10)
        self.status.bind("<Configure>", lambda e: self.status.config(wraplength=max(200, e.width - 4)))

        entry_row = tk.Frame(self.root, bg=BG)
        entry_row.pack(side="bottom", fill="x", padx=8, pady=8)
        self.entry = tk.Entry(entry_row, bg=BG2, fg=FG, insertbackground=FG, relief="flat", font=(FONT, 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", lambda e: self.send_reply())
        tk.Button(entry_row, text=f"翻成{LANGS[self.cfg['friend_lang']]['name']}", command=self.send_reply, bg=ACCENT, fg=FG,
                  relief="flat", bd=0, padx=10, font=(FONT, 9), cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Label(self.root, text="↓ 在這裡輸入你要說的話，按 Enter 翻譯並自動複製", bg=BG, fg=DIM, anchor="w",
                 font=(FONT, 8)).pack(side="bottom", fill="x", padx=10)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        scroll = tk.Scrollbar(body)
        scroll.pack(side="right", fill="y")
        self.text = tk.Text(body, bg=BG2, fg=FG, relief="flat", wrap="word", state="disabled", padx=8, pady=6,
                            yscrollcommand=scroll.set, font=(FONT, 12), spacing3=2)
        self.text.pack(fill="both", expand=True)
        scroll.config(command=self.text.yview)
        self.text.tag_configure("orig", foreground=DIM, font=(FONT, 9))
        self.text.tag_configure("them", foreground=FG, font=(FONT, 13), spacing3=10)
        self.text.tag_configure("me", foreground="#8ea1ff", font=(FONT, 13), spacing3=10)
        self.text.tag_configure("note", foreground=DIM, font=(FONT, 9), spacing3=8)
        self.text.tag_configure("tight", spacing3=1)
        self.text.tag_configure("roman", foreground="#c9cdfb", font=(FONT, 10), spacing3=10)

    def _exclude_from_capture(self):
        """讓這個視窗不會被自己的螢幕擷取拍進去（Windows 10 2004 以上）。"""
        if not IS_WIN:
            return
        try:
            import ctypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _minimize_console():
        """視窗出來之後，把 start.bat 的黑色視窗縮到工作列。"""
        if not IS_WIN:
            return
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:  # noqa: BLE001
            pass

    def set_status(self, msg: str):
        self.ui_q.put(("status", msg))

    def add_entry(self, tag: str, original: str, translated: str, pronunciation: str = ""):
        self.ui_q.put(("entry", tag, original, translated, pronunciation))

    def _pronunciation(self, text: str, lang: str) -> str:
        """韓文才有唸法；其他語言回傳空字串。"""
        if lang != "ko" or not self.cfg["show_pronunciation"]:
            return ""
        return "唸法：" + romanize(text)

    def _drain_ui(self):
        try:
            while True:
                item = self.ui_q.get_nowait()
                if item[0] == "status":
                    self.status.config(text=item[1])
                elif item[0] == "entry":
                    _, tag, original, translated, pronunciation = item
                    self.text.config(state="normal")
                    if original:
                        self.text.insert("end", original + "\n", "orig")
                    if pronunciation:   # 譯文和唸法貼在一起，中間不要多空一行
                        self.text.insert("end", translated + "\n", (tag, "tight"))
                        self.text.insert("end", pronunciation + "\n", "roman")
                    else:
                        self.text.insert("end", translated + "\n", tag)
                    self.text.config(state="disabled")
                    self.text.see("end")
                elif item[0] == "copy":  # 把翻好的話放進剪貼簿（一定要在主執行緒做）
                    self.root.clipboard_clear()
                    self.root.clipboard_append(item[1])
                    self.root.update_idletasks()
                    self.last_clip = item[1]
                elif item[0] == "voice":  # 語音開關的按鈕文字和顏色
                    self._paint_voice_button(item[1])
                elif item[0] == "devices":
                    self._show_device_dialog(item[1])
                elif item[0] == "error":
                    messagebox.showerror("即時翻譯", item[1], parent=self.root)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self._drain_ui)

    def clear(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def _save_flags(self):
        self.cfg["clipboard"] = bool(self.clip_var.get())
        self.save_config()

    def save_config(self):
        try:
            self.cfg["geometry"] = self.root.geometry()
            CONFIG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 啟動背景元件 ----------

    def _boot(self):
        try:
            self.set_status("正在啟動翻譯引擎（要把模型讀進記憶體，大約半分鐘到一分鐘）…")
            self.engine.start()
            if not self.engine.wait_ready():
                raise RuntimeError("翻譯引擎沒有在時間內準備好，詳細原因在 engine.log")
            self.set_status("正在載入文字辨識…")
            from core.ocr import OcrEngine
            from core.watcher import ScreenWatcher

            ocr = OcrEngine(ROOT / "models" / "ocr", scale=float(self.cfg["ocr_scale"]))
            langs = {LANGS[self.cfg["friend_lang"]]["family"]} | ({"en"} if self.cfg["also_english"] else set())
            self.watcher = ScreenWatcher(ocr, on_message=lambda text, lang: self.jobs.put(("screen", text, lang)),
                                         on_status=self.set_status, interval=float(self.cfg["interval"]),
                                         source_langs=langs)
            self.watcher.set_region(self.cfg["region"])
            self.watcher.start()
            self.set_status(self._idle_status())
        except Exception as ex:  # noqa: BLE001
            log_error(traceback.format_exc())
            self.set_status(f"啟動失敗：{ex}")
            self.ui_q.put(("error", f"啟動失敗：{ex}"))

    def _idle_status(self) -> str:
        parts = []
        for k in ("loopback", "mic"):
            if k in self.pipelines:
                name = self.pipelines[k].capture.device_name
                parts.append(self.VOICE_NAMES[k] + (f"（{name}）" if name else ""))
        voice = "　語音：" + "、".join(parts) + "。" if parts else ""
        if not self.cfg["region"]:
            if voice:
                return "已就緒。" + voice.strip()
            return "已就緒。請按「框選聊天區」，把聊天訊息的範圍框起來。"
        r = self.cfg["region"]
        return f"正在看螢幕上 {r['width']}×{r['height']} 的區域。" + voice

    # ---------- 翻譯 ----------

    def _translate_worker(self):
        my_family = LANGS[self.cfg["my_lang"]]["family"]
        while True:
            kind, text, lang = self.jobs.get()
            try:
                if kind in ("reply", "spoken"):
                    # 你打的字、你說的話：是自己的語言就翻成對方的語言，否則翻回自己的語言
                    target = self.cfg["friend_lang"] if lang == my_family else self.cfg["my_lang"]
                    out = self.translator.translate(text, target, lang)
                    if target != self.cfg["my_lang"] and (kind == "reply" or self.cfg["copy_spoken"]):
                        self.own_clip = out
                        self.ui_q.put(("copy", out))
                    label = "［我說］" + text if kind == "spoken" else text
                    self.add_entry("me", label, out, self._pronunciation(out, LANGS[target]["family"]))
                else:
                    out = self.translator.translate(text, self.cfg["my_lang"], lang)
                    if out and out.strip() != text.strip():
                        self.add_entry("them", "［聽到］" + text if kind == "heard" else text, out)
            except Exception as ex:  # noqa: BLE001
                log_error(traceback.format_exc())
                self.add_entry("note", text, f"（翻譯失敗：{ex}）")

    def send_reply(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.jobs.put(("reply", text, detect_language(text) or "en"))

    def _poll_clipboard(self):
        if self.clip_var.get():
            try:
                clip = self.root.clipboard_get()
            except tk.TclError:
                clip = ""
            if clip and clip != self.last_clip:
                self.last_clip = clip
                text = clip.strip()
                lang = detect_language(text)
                my_family = LANGS[self.cfg["my_lang"]]["family"]
                # 自己語言的文字、剛剛翻好的回覆、太長的內容都不翻
                if lang and lang != my_family and text != self.own_clip and len(text) <= 1500:
                    self.jobs.put(("clip", text, lang))
        if not self.closing:
            self.root.after(500, self._poll_clipboard)

    # ---------- 語音 ----------

    VOICE_NAMES = {"loopback": "聽對方說話", "mic": "聽我說話"}

    def _paint_voice_button(self, kind: str):
        on = kind in self.pipelines
        self.voice_buttons[kind].config(text=f"{self.VOICE_NAMES[kind]}：{'開' if on else '關'}", bg=ACCENT if on else BG2)

    def make_capture(self, kind: str):
        """建立收音來源（測試時會換成假的）。"""
        from core.audio import Capture

        return Capture(kind, on_error=self._voice_error,
                       device=self.cfg["speaker_device"] if kind == "loopback" else self.cfg["mic_device"],
                       on_open=lambda k, name: self.set_status(self._idle_status()))

    def _voice_langs(self, kind: str) -> list[str]:
        my_family = LANGS[self.cfg["my_lang"]]["family"]
        friend_family = LANGS[self.cfg["friend_lang"]]["family"]
        if kind == "loopback":
            return list(self.cfg["listen_langs"] or [friend_family])
        return list(self.cfg["mic_langs"] or [my_family, friend_family])

    def toggle_voice(self, kind: str):
        if kind in self.pipelines:
            self.pipelines.pop(kind).stop()
            self._paint_voice_button(kind)
            self.set_status(self._idle_status())
            return
        if getattr(self, "_voice_starting", False):   # 正在載入模型，先不要重複按
            return
        self._voice_starting = True
        threading.Thread(target=self._start_voice, args=(kind,), daemon=True).start()

    def _start_voice(self, kind: str):
        try:
            from core.speech import Recognizer, SpeechPipeline

            if self.recognizer is None:
                model_dir = ROOT / "models" / "whisper"
                if not (model_dir / "model.bin").exists():
                    raise RuntimeError("找不到語音辨識模型，請再執行一次 setup.bat 把它下載回來")
                self.set_status("正在載入語音辨識（約 10 秒）…")
                self.recognizer = Recognizer(model_dir, threads=int(self.cfg["whisper_threads"]))
            pipe = SpeechPipeline(self.make_capture(kind), self.recognizer, self._voice_langs(kind),
                                  on_text=self._on_speech, on_state=self._on_speaking,
                                  end_silence=float(self.cfg["end_silence"]))
            pipe.start()
            self.pipelines[kind] = pipe
            self.ui_q.put(("voice", kind))
            self.set_status(self._idle_status())
        except Exception as ex:  # noqa: BLE001
            log_error(traceback.format_exc())
            self.set_status(f"{self.VOICE_NAMES[kind]}開不起來：{ex}")
        finally:
            self._voice_starting = False

    def _on_speech(self, kind: str, text: str, lang: str, seconds: float = 0.0):
        if lang == "error":
            self.add_entry("note", "", text)
            return
        self.jobs.put(("heard" if kind == "loopback" else "spoken", text, lang))

    def _on_speaking(self, kind: str, speaking: bool):
        if speaking:
            self.set_status("對方正在說話…" if kind == "loopback" else "聽到你在說話…")
        else:
            self.set_status(self._idle_status())

    def _voice_error(self, kind: str, ex: Exception):
        log_error(f"{kind} capture failed: {ex!r}")
        pipe = self.pipelines.pop(kind, None)
        if pipe:
            pipe.stop()
        self.ui_q.put(("voice", kind))
        what = "電腦播放的聲音" if kind == "loopback" else "麥克風"
        self.add_entry("note", "", f"（收不到{what}：{ex}）")
        self.set_status(f"{self.VOICE_NAMES[kind]}已關閉：收音失敗，詳細原因在 app.log")

    # ---------- 選擇收音裝置 ----------

    @staticmethod
    def list_audio_devices() -> dict:
        from core.audio import list_devices

        return list_devices()

    def choose_devices(self):
        """按「裝置」：先在背景查出這台電腦有哪些裝置，再跳出選單。"""
        def work():
            try:
                self.ui_q.put(("devices", self.list_audio_devices()))
            except Exception as ex:  # noqa: BLE001
                log_error(traceback.format_exc())
                self.set_status(f"讀不到音訊裝置：{ex}")

        threading.Thread(target=work, daemon=True).start()

    def _show_device_dialog(self, info: dict):
        if getattr(self, "_device_dialog", None) is not None and self._device_dialog.winfo_exists():
            self._device_dialog.lift()
            return
        dlg = tk.Toplevel(self.root)
        self._device_dialog = dlg
        dlg.title("選擇收音裝置")
        dlg.configure(bg=BG, padx=14, pady=12)
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        dlg.geometry(f"+{self.root.winfo_x() + 30}+{self.root.winfo_y() + 70}")
        combos = {}
        rows = (("speaker_device", "聽對方說話：要聽哪個播放裝置（你耳機或喇叭的名字）", info["speakers"], info["default_speaker"]),
                ("mic_device", "聽我說話：要用哪支麥克風", info["microphones"], info["default_microphone"]))
        for key, title, names, default in rows:
            tk.Label(dlg, text=title, bg=BG, fg=FG, anchor="w", font=(FONT, 10)).pack(fill="x", pady=(4, 2))
            values = [f"跟著系統預設（目前是 {default}）"] + list(names)
            box = ttk.Combobox(dlg, values=values, state="readonly", width=56, font=(FONT, 10))
            current = self.cfg[key]
            box.current(values.index(current) if current in names else 0)
            box.pack(fill="x", pady=(0, 8))
            combos[key] = (box, list(names))
        tk.Label(dlg, text="選的是「Discord 把對方聲音播出來的那個裝置」。不確定的話，看 Discord 的\n"
                           "使用者設定 → 語音與視訊 → 輸出裝置 / 輸入裝置。",
                 bg=BG, fg=DIM, justify="left", anchor="w", font=(FONT, 9)).pack(fill="x", pady=(0, 10))

        def apply():
            changed = []
            for key, (box, names) in combos.items():
                i = box.current()
                value = names[i - 1] if i > 0 else None
                if value != self.cfg[key]:
                    self.cfg[key] = value
                    changed.append("loopback" if key == "speaker_device" else "mic")
            dlg.destroy()
            self.save_config()
            restart = [k for k in changed if k in self.pipelines]
            if restart:   # 正在聽的話，換成新裝置重新開始
                threading.Thread(target=self._restart_voice, args=(restart,), daemon=True).start()
            elif changed:
                self.set_status("裝置已更新，下次開啟語音就會用新的裝置。")

        row = tk.Frame(dlg, bg=BG)
        row.pack(fill="x")
        tk.Button(row, text="確定", command=apply, bg=ACCENT, fg=FG, relief="flat", bd=0, padx=16, pady=3,
                  font=(FONT, 10), cursor="hand2").pack(side="right")
        tk.Button(row, text="取消", command=dlg.destroy, bg=BG2, fg=FG, relief="flat", bd=0, padx=16, pady=3,
                  font=(FONT, 10), cursor="hand2").pack(side="right", padx=(0, 6))
        self._device_combos = combos      # 給測試用
        self._device_apply = apply

    def _restart_voice(self, kinds: list[str]):
        for kind in kinds:
            pipe = self.pipelines.pop(kind, None)
            if pipe:
                pipe.stop()
        time.sleep(0.6)                   # 等舊的收音真的停下來
        for kind in kinds:
            self._start_voice(kind)

    # ---------- 框選區域 ----------

    def toggle_pause(self):
        if not self.watcher:
            return
        self.watcher.paused = not self.watcher.paused
        self.pause_btn.config(text="繼續" if self.watcher.paused else "暫停")
        self.set_status("已暫停，不會看螢幕。" if self.watcher.paused else self._idle_status())

    def select_region(self):
        left, top, width, height = virtual_screen()
        sel = tk.Toplevel(self.root)
        sel.overrideredirect(True)
        sel.attributes("-topmost", True)
        try:
            sel.attributes("-alpha", 0.3)
        except tk.TclError:
            pass
        if width and height:
            sel.geometry(f"{width}x{height}+{left}+{top}")
        else:
            left = top = 0
            sel.geometry(f"{sel.winfo_screenwidth()}x{sel.winfo_screenheight()}+0+0")
        canvas = tk.Canvas(sel, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(40, 40, anchor="nw", fill="white", font=(FONT, 20),
                           text="按住滑鼠左鍵，把聊天訊息的範圍框起來（按 Esc 取消）")
        state = {}

        def press(e):
            state["x"], state["y"] = e.x, e.y
            state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#00ff88", width=3)

        def drag(e):
            if "rect" in state:
                canvas.coords(state["rect"], state["x"], state["y"], e.x, e.y)

        def release(e):
            if "rect" not in state:
                return
            x1, x2 = sorted((state["x"], e.x))
            y1, y2 = sorted((state["y"], e.y))
            sel.destroy()
            if x2 - x1 < 60 or y2 - y1 < 30:
                self.set_status("框選的範圍太小，請再框一次。")
                return
            self.cfg["region"] = {"left": left + x1, "top": top + y1, "width": x2 - x1, "height": y2 - y1}
            self.save_config()
            if self.watcher:
                self.watcher.set_region(self.cfg["region"])
            self.set_status(self._idle_status())

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        sel.bind("<Escape>", lambda e: sel.destroy())
        sel.focus_force()

    # ---------- 結束 ----------

    def close(self):
        self.closing = True
        self.save_config()
        if self.watcher:
            self.watcher.stop()
        for pipe in list(self.pipelines.values()):
            pipe.stop()
        self.engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        App().run()
    except Exception:  # noqa: BLE001
        log_error(traceback.format_exc())
        raise
