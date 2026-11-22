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
    soft = {"audio_devices", "mic_capture", "screen_grab", "engine_online", "tray", "app_audio"}     # 這幾項跟電腦環境有關，失敗只記錄、不算錯

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

    def second_mic_way():
        """第二種收音方式（麥克風的備援）在打包後也載得起來。"""
        import sounddevice as sd

        return {"version": sd.__version__, "interfaces": [a["name"] for a in sd.query_hostapis()]}

    def mic_capture():
        """有麥克風的電腦上真的收一秒聲音，記下用的是哪一種方式。"""
        import time

        from .core.audio import Capture

        errors = []
        cap = Capture("mic", on_error=lambda kind, ex: errors.append(str(ex)))
        cap.start()
        time.sleep(2.0)
        cap.stop()
        if errors:
            raise RuntimeError(errors[0])
        return {"device": cap.device_name, "way": cap.backend, "blocks": cap.q.qsize()}

    def app_audio():
        """只收單一程式的聲音：另外開一個程序試收自己的聲音，記下 Windows 給不給、收到多少。"""
        import subprocess

        from .core import procaudio

        if not procaudio.available():
            return {"available": False}
        result = tmp / "app-audio.json"
        head = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "livelingo"]
        # 三個輸出入都要指定：沒有主控台的打包版從某些地方啟動時，沒指定的那個會讓子程序開不起來
        done = subprocess.run([*head, "--probe-app-audio", str(result)], timeout=60, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        info = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}
        info["exit"] = done.returncode
        assert done.returncode == 0 and info.get("opened") and not info.get("error"), info
        return info

    def screen_grab():
        import mss
        import numpy as np

        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            shot = np.asarray(sct.grab({"left": 0, "top": 0, "width": 200, "height": 100}))
        return list(shot.shape)

    def floating_chat():
        """浮動留言：畫得出來，而且（在 Windows 上）真的能開出透明、滑鼠會穿過去的視窗。"""
        from PIL import Image

        from . import chatpaint, overlay

        tile = chatpaint.paint_message("heard", "對方", "打野來一下下路 정글 와줘 OK!", 520, 26)
        frame = chatpaint.compose([(tile, 1.0)], (520, 160))
        assert tile.getchannel("A").getextrema()[1] == 255
        info = {"tile": list(tile.size), "fonts": {k: bool(chatpaint._font_path(k)) for k in ("cjk", "ko", "latin")}}
        # 三種字型在這台電腦上真的畫出來的樣子（明體、楷體只有 Windows 有，存成圖讓人看得到）
        from .config import DEFAULTS

        info["families"] = {}
        for family in chatpaint.FAMILIES:
            look = dict(DEFAULTS, ui_lang="zh-TW", overlay_family=family, overlay_backdrop=0 if family == "sans" else 30)
            info["families"][family] = Path(chatpaint._font_path("cjk", family, False) or "").name
            try:
                chatpaint.preview(look).save(out.with_name(f"floating-chat-look-{family}.png"))
            except OSError:
                pass
        if overlay.LayeredWindow is None:
            return info
        import ctypes

        rect = [120, 140, 520, 160]
        moved = []
        win = overlay.LayeredWindow(on_rect=lambda r, final: moved.append((r, final)))
        assert win.start(rect), win.error
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowDisplayAffinity(ctypes.c_void_p(win.hwnd), 0)      # 檢查時要拍得到自己
            solid = Image.new("RGBA", (rect[2], rect[3]), (255, 0, 255, 255))
            solid.alpha_composite(frame)
            win.paint(solid, rect)
            deadline = time.time() + 3
            while win.frames < 1 and time.time() < deadline:
                time.sleep(0.05)
            assert win.frames >= 1, win.error or "nothing was painted"
            info["rect"] = win.window_rect()
            assert info["rect"] == rect, info["rect"]
            style = overlay._u32.GetWindowLongPtrW(win.hwnd, -20)
            assert style & 0x20 and style & 0x80000 and style & 0x8, hex(style)      # 穿透、分層、最上層
            info["scale"] = win.scale
            try:      # 螢幕上真的看得到嗎（跟環境有關，只記錄）
                import mss
                import numpy as np

                time.sleep(0.4)
                with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                    shot = np.asarray(sct.grab({"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}))
                    info["seen_on_screen"] = [int(v) for v in shot[4, rect[2] - 6][:3]] == [255, 0, 255]
                    win.paint(frame, rect)
                    time.sleep(0.5)
                    shot = sct.grab({"left": rect[0] - 40, "top": rect[1] - 40, "width": rect[2] + 80, "height": rect[3] + 80})
                    Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out.with_name("floating-chat.png"))
            except Exception as ex:  # noqa: BLE001
                info["seen_on_screen"] = repr(ex)
            win.set_edit(True)
            time.sleep(0.4)
            assert win.editing and not overlay._u32.GetWindowLongPtrW(win.hwnd, -20) & 0x20      # 調整位置時抓得到
            win.set_edit(False)
            time.sleep(0.4)
            assert overlay._u32.GetWindowLongPtrW(win.hwnd, -20) & 0x20
            assert not win.error, win.error
        finally:
            win.close()
        assert not win.hwnd
        return info

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
        for f in ("app.js", "brand.js", "i18n.js", "style.css"):
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
    check("second_mic_way", second_mic_way)
    check("mic_capture", mic_capture)
    check("app_audio", app_audio)
    check("screen_grab", screen_grab)
    check("floating_chat", floating_chat)

    def tray():
        if sys.platform != "win32":
            return
        ok = state["service"].tray.start()
        assert ok, state["service"].tray.error
        time.sleep(1.0)
        assert state["service"].tray.running

    check("tray", tray)
    check("gpu", gpu_detect)
    if "--online" in sys.argv:
        check("engine_online", engine_online)
    check("server", server)

    if window and "service" in state:
        from . import window as window_mod
        from .config import UI_LANGS

        service, srv = state["service"], state["server"]
        info: dict = {}
        done = threading.Event()

        def snap(name: str) -> None:
            import mss
            from PIL import Image

            w, h, x, y = service.window.geometry()
            with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                shot = sct.grab({"left": max(0, x - 8), "top": max(0, y - 8), "width": w + 16, "height": h + 16})
                Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out.with_name(name))

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
                info["cards"] = win.evaluate_js("document.querySelectorAll('.ui-langs button').length")      # 介面語言的按鈕
                time.sleep(0.5)
                info["has_window_ctl"] = service.window is not None
                if service.window:
                    info["size_before"] = service.window.geometry()
                    service.set_view("main")
                    time.sleep(1.0)
                    info["size_main"] = service.window.geometry()
                    service.set_view("wizard")
                    time.sleep(1.0)
                    info["size_after"] = service.window.geometry()
                    # 把真的視窗拍下來（含 Windows 的標題列），之後看得到外觀在 Windows 上是不是對的；拍不到不算錯
                    try:
                        info["title_bar"] = service.window.set_chrome(*service.chrome)
                        snap("window-wizard.png")
                        service.cfg.set(setup_done=True)
                        service.set_view("main")
                        time.sleep(1.5)
                        snap("window-main.png")
                        service.update_config({"theme": "light", "accent": "violet"})
                        time.sleep(1.5)
                        info["chrome_light"] = list(service.chrome)
                        snap("window-light.png")
                    except Exception as ex:  # noqa: BLE001
                        info["snapshots"] = repr(ex)
                    finally:
                        service.update_config({"theme": "dark", "accent": "mint"})
                        service.cfg.set(setup_done=False)
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
                  and info.get("cards") == len(UI_LANGS) and info.get("has_window_ctl"))
        results["window"] = {"ok": ok, "info": info, "opened": opened, "seconds": round(time.time() - started, 2)}
        if not ok and "error" in info:
            results["window"]["error"] = info["error"]
    if "service" in state:
        state["service"].shutdown()
    failed = save()
    return 1 if failed else 0
