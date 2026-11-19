"""
自我檢查：確認打包好的程式在這台電腦上真的跑得起來（元件都在、文字辨識能用、介面視窗開得起來）。
給自動打包流程用：LiveLingo.exe --selftest 結果.json [--no-window] [--online]
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path


def run(out: Path, window: bool = True) -> int:
    results: dict = {}
    soft = {"audio_devices", "screen_grab", "engine_online"}     # 這幾項跟電腦環境有關，失敗只記錄、不算錯

    def check(name, fn):
        started = time.time()
        try:
            results[name] = {"ok": True, "info": fn(), "seconds": round(time.time() - started, 2)}
        except Exception:  # noqa: BLE001
            results[name] = {"ok": False, "error": traceback.format_exc()[-1500:], "seconds": round(time.time() - started, 2)}
        save()

    def save():
        failed = [k for k, v in results.items() if not v["ok"] and k not in soft]
        out.write_text(json.dumps({"ok": not failed, "failed": failed, "results": results}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return failed

    tmp = Path(tempfile.mkdtemp(prefix="livelingo-selftest-"))
    os.environ["LIVELINGO_DATA"] = str(tmp)
    os.environ["LIVELINGO_NO_UPDATE"] = "1"

    def imports():
        import ctranslate2
        import cv2
        import faster_whisper
        import mss  # noqa: F401
        import numpy
        import onnxruntime
        import opencc  # noqa: F401
        import rapidocr  # noqa: F401

        import tkinter

        from . import __version__, oswin

        return {"version": __version__, "tk": tkinter.Tcl().eval("info patchlevel"), "webview2": oswin.has_webview2(), "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0],
                "numpy": numpy.__version__, "cv2": cv2.__version__, "onnxruntime": onnxruntime.__version__,
                "ctranslate2": ctranslate2.__version__, "faster_whisper": faster_whisper.__version__}

    def text_tools():
        from .core.romanize import romanize
        from .core.translate import _to_traditional, build_prompt
        from .terms import TermPack

        assert _to_traditional.convert("软件") == "軟體"
        assert romanize("같이") == "gachi"
        hints = TermPack().find("탑 갈게", "ko", "zh-TW")
        assert hints == [("탑", "上路")], hints
        assert "上路" in build_prompt("탑 갈게", "ko", "zh-TW", {}, "casual", hints)
        return "ok"

    def clipboard():
        from . import oswin

        text = "LiveLingo 잠깐만 等我一下"
        assert oswin.copy_text(text), "copy_text returned False"
        got = oswin.read_clipboard()
        assert got == text, repr(got)
        return "ok"

    def single_instance():
        from . import oswin

        first = oswin.acquire_single_instance()
        oswin.release_single_instance()
        return {"acquired": first}

    def ocr_builtin():
        import cv2
        import numpy as np

        from .core.ocr import OcrEngine, message_lines

        img = np.full((120, 640, 3), 40, dtype=np.uint8)
        cv2.putText(img, "Hello LiveLingo 2024", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (230, 230, 230), 2, cv2.LINE_AA)
        lines = [ln.text for ln in message_lines(OcrEngine(None, 1.5, family="en").read(img))]
        assert any("LiveLingo" in ln for ln in lines), lines
        return lines

    def speech_parts():
        import numpy as np

        from .core.audio import SR
        from .core.speech import Segmenter

        seg = Segmenter()
        tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR * 2) / SR)).astype(np.float32)
        got = seg.feed(np.concatenate([np.zeros(SR, dtype=np.float32), tone, np.zeros(SR * 2, dtype=np.float32)]))
        return {"segments": len(got)}      # 純音不是人聲，切不出句子是正常的；重點是語音偵測的模型載得起來

    def audio_devices():
        from .service import RealAudio

        found = RealAudio().devices()
        return {"speakers": len(found["speakers"]), "microphones": len(found["microphones"])}

    def screen_grab():
        import mss
        import numpy as np

        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            shot = np.asarray(sct.grab({"left": 0, "top": 0, "width": 200, "height": 100}))
        return list(shot.shape)

    def gpu_detect():
        from . import gpu

        found = gpu.detect()
        return {**found, "mode": gpu.choose_mode("auto", found)}

    def engine_online():
        from . import assets

        releases = assets.list_engine_releases()
        picked = {kind: [n for n, _ in (assets.pick_engine_assets(releases, kind) or [])] for kind in ("cpu", "cuda")}
        assert picked["cpu"] and len(picked["cuda"]) == 2, picked
        if sys.platform == "win32":
            import subprocess

            exe = assets.get_engine(tmp, "cpu")
            ver = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60,
                                 creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
            picked["version"] = (ver.stdout + ver.stderr).strip()[:200]
            assert ver.returncode == 0, picked
        return picked

    state: dict = {}

    def server():
        from .server import Server, ui_files
        from .service import Service

        service = Service(tmp)
        srv = Server(service)
        srv.start()
        service.start()
        state.update(service=service, server=srv)
        base = f"http://127.0.0.1:{srv.port}"
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            assert b"LiveLingo" in r.read()
        for f in ("app.js", "i18n.js", "style.css"):
            with urllib.request.urlopen(f"{base}/{f}", timeout=10) as r:
                assert r.status == 200 and len(r.read()) > 1000
        try:
            urllib.request.urlopen(base + "/api/state", timeout=10)
            raise AssertionError("the API answered without the token")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        with urllib.request.urlopen(f"{base}/api/state?t={srv.token}", timeout=10) as r:
            st = json.loads(r.read().decode("utf-8"))
        deadline = time.time() + 30
        while service.phase in ("starting", "updating") and time.time() < deadline:
            time.sleep(0.3)
        return {"ui_files": len(ui_files()), "phase": service.phase, "ui_lang": st["config"]["ui_lang"],
                "need_mb": service.detail.get("total_mb")}

    check("imports", imports)
    check("text_tools", text_tools)
    check("clipboard", clipboard)
    check("single_instance", single_instance)
    check("ocr_builtin", ocr_builtin)
    check("speech_parts", speech_parts)
    check("audio_devices", audio_devices)
    check("screen_grab", screen_grab)
    check("gpu", gpu_detect)
    if "--online" in sys.argv:
        check("engine_online", engine_online)
    check("server", server)

    if window and "service" in state:
        from . import window as window_mod

        service, srv = state["service"], state["server"]
        info: dict = {}
        done = threading.Event()

        def on_loaded(win):
            if done.is_set():
                return
            try:
                deadline = time.time() + 30
                text = ""
                while time.time() < deadline:
                    text = win.evaluate_js("document.getElementById('wizard') ? document.getElementById('wizard').innerText : ''") or ""
                    if len(text) > 20:
                        break
                    time.sleep(0.5)
                info["wizard_text"] = text[:80]
                info["cards"] = win.evaluate_js("document.querySelectorAll('.ui-lang').length")
                time.sleep(0.5)
                info["has_window_ctl"] = service.window is not None
                if service.window:
                    info["size_before"] = service.window.geometry()
                    service.set_view("subtitle")
                    time.sleep(1.0)
                    info["size_subtitle"] = service.window.geometry()
                    service.set_view("wizard")
                    time.sleep(1.0)
                    info["size_after"] = service.window.geometry()
            except Exception:  # noqa: BLE001
                info["error"] = traceback.format_exc()[-1500:]
            finally:
                done.set()
                try:
                    win.destroy()
                except Exception:  # noqa: BLE001
                    pass

        def watchdog():
            if not done.wait(90):
                results["window"] = {"ok": False, "error": "the window did not load within 90 seconds", "info": info}
                save()
                os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()
        started = time.time()
        opened = window_mod._run_webview(srv.url, service, on_loaded)
        ok = bool(opened and done.is_set() and "error" not in info and len(info.get("wizard_text", "")) > 20
                  and info.get("cards") == 4 and info.get("has_window_ctl"))
        results["window"] = {"ok": ok, "info": info, "opened": opened, "seconds": round(time.time() - started, 2)}
        if not ok and "error" in info:
            results["window"]["error"] = info["error"]
    if "service" in state:
        state["service"].shutdown()
    failed = save()
    return 1 if failed else 0
