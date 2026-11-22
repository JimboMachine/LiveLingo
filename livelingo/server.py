"""
介面和程式主體之間的橋樑：只開在這台電腦上（127.0.0.1）的小型網頁伺服器。
介面是一個網頁，用這裡的 API 按按鈕、用事件串流（SSE）接收新的翻譯。
每次啟動會產生一組隨機的通行碼，沒有通行碼的請求一律拒絕，別的網頁或程式碰不到。
"""

import json
import mimetypes
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import DEFAULTS, OVERLAY_LOOK
from .paths import resource_dir
from .service import Service

UI_DIR = resource_dir() / "ui"


class Server:
    def __init__(self, service: Service, port: int = 0):
        self.service = service
        self.token = secrets.token_urlsafe(24)
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="http").start()

    def stop(self) -> None:
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    # ------------------------------------------------------------------

    def _make_handler(self):
        server = self
        service = self.service

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):   # 不要把每個請求印出來
                pass

            # ---- 共用 ----

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                return host in (f"127.0.0.1:{server.port}", f"localhost:{server.port}")

            def _authorized(self, query: dict) -> bool:
                token = self.headers.get("X-LiveLingo-Token") or (query.get("t") or [""])[0]
                return secrets.compare_digest(token, server.token)

            def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, value, code: int = 200) -> None:
                self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"))

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2_000_000:      # 匯入的詞彙檔可能有幾百句
                    return {}
                try:
                    value = json.loads(self.rfile.read(length).decode("utf-8"))
                    return value if isinstance(value, dict) else {}
                except Exception:  # noqa: BLE001
                    return {}

            # ---- GET ----

            def do_GET(self):   # noqa: N802
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not self._host_ok():
                    return self._send(403, b"forbidden", "text/plain")
                if url.path == "/api/state":
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.state())
                if url.path == "/api/devices":
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.devices())
                if url.path == "/api/apps":      # 現在開著哪些程式（「只聽這幾個程式」的清單）
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.apps_state())
                if url.path == "/api/health":      # 健康檢查：每一項一個燈
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.health())
                if url.path == "/api/usage":      # 使用數字：今天、最近 7 天、30 天各幾句、失敗幾次、平均等多久
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.usage_state())
                if url.path == "/api/storage":      # 儲存空間：放在哪、每一樣佔多少、還剩多少
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.storage_state())
                if url.path == "/api/history":      # 對話歷史：q 搜尋的字、offset 從第幾則開始、limit 幾則（最新的在前）
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    one = lambda name, default: (query.get(name) or [default])[0]      # noqa: E731
                    try:
                        offset, limit = int(one("offset", "0")), int(one("limit", "50"))
                    except ValueError:
                        offset, limit = 0, 50
                    return self._json({**service.history.search(one("q", ""), offset, limit), **service.history.state()})
                if url.path == "/api/screens":      # 接著哪些螢幕、留言現在在哪（設定頁的縮小螢幕圖）
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._json(service.overlay.screens())
                if url.path == "/api/overlay_preview":      # 設定畫面上的浮動留言預覽（照現在的設定畫的一張圖）
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._send(200, service.overlay_preview(), "image/png")
                if url.path == "/api/events":
                    if not self._authorized(query):
                        return self._json({"error": "unauthorized"}, 401)
                    return self._events()
                return self._static(url.path)

            def _static(self, path: str) -> None:
                name = "index.html" if path in ("/", "") else path.lstrip("/")
                target = (UI_DIR / name).resolve()
                if UI_DIR.resolve() not in target.parents or not target.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                self._send(200, target.read_bytes(), ctype)

            def _events(self) -> None:
                q = service.hub.subscribe()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while not service.closing.is_set():
                        try:
                            event, data = q.get(timeout=10)
                            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                        except queue.Empty:
                            chunk = ": ping\n\n"
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
                finally:
                    service.hub.unsubscribe(q)
                    self.close_connection = True

            # ---- POST ----

            def do_POST(self):   # noqa: N802
                url = urlparse(self.path)
                if not self._host_ok():
                    return self._send(403, b"forbidden", "text/plain")
                if not self._authorized(parse_qs(url.query)):
                    return self._json({"error": "unauthorized"}, 401)
                body = self._body()
                route = url.path
                try:
                    if route == "/api/config":
                        result = service.update_config(body)
                        return self._json({"ok": True, **result, "config": service.cfg.snapshot()})
                    if route == "/api/source":
                        service.set_source(str(body.get("key")), bool(body.get("on")))
                        return self._json({"ok": True})
                    if route == "/api/send":
                        service.submit("typed", str(body.get("text") or "")[:500])
                        return self._json({"ok": True})
                    if route == "/api/region":
                        service.select_region(then_enable=bool(body.get("enable", True)),
                                              rid=str(body["id"]) if body.get("id") else None, new=bool(body.get("new")))
                        return self._json({"ok": True})
                    if route == "/api/ocr_test":
                        return self._json(service.ocr_test(str(body["id"]) if body.get("id") else None))
                    if route == "/api/meters":
                        service.set_meters(bool(body.get("on")))
                        return self._json({"ok": True})
                    if route == "/api/find_mic":
                        if body.get("cancel"):
                            service.cancel_find_mic()
                            return self._json({"ok": True})
                        return self._json({"ok": service.find_mic()})
                    if route == "/api/mic_settings":
                        return self._json({"ok": service.open_mic_settings()})
                    if route == "/api/download":
                        service.start_download()
                        return self._json({"ok": True})
                    if route == "/api/fix":
                        ok = service.fix(int(body.get("id") or 0), str(body.get("src") or ""), str(body.get("dst") or ""))
                        return self._json({"ok": ok})
                    if route == "/api/edit":      # 改辨識出來的原文，重新翻
                        return self._json({"ok": service.edit(int(body.get("id") or 0), str(body.get("text") or ""))})
                    if route == "/api/teach":
                        return self._json({"ok": service.teach(int(body.get("id") or 0), str(body.get("text") or "")[:500])})
                    if route == "/api/lessons/remove":
                        service.lesson_remove(str(body.get("src") or ""), str(body.get("target") or ""))
                        return self._json({"ok": True})
                    if route == "/api/glossary/add":
                        return self._json({"ok": service.glossary_add(str(body.get("src") or ""), str(body.get("dst") or ""))})
                    if route == "/api/glossary/remove":
                        service.glossary_remove(str(body.get("src") or ""))
                        return self._json({"ok": True})
                    if route == "/api/speech_gpu":      # 語音辨識用顯示卡：下載需要的檔案
                        return self._json({"ok": service.speech_gpu_download(cancel=bool(body.get("cancel")))})
                    if route == "/api/library":      # 詞彙表和教過的句子：匯出成檔案、從檔案匯入
                        if body.get("export"):
                            return self._json(service.export_library())
                        return self._json(service.import_library(body.get("data")))
                    if route == "/api/copy":
                        return self._json({"ok": service.copy(int(body.get("id") or 0))})
                    if route == "/api/settings":      # 設定的匯出、匯入、恢復預設、復原
                        if body.get("export"):
                            return self._json(service.settings_export())
                        if body.get("reset"):
                            return self._json(service.settings_reset())
                        if body.get("undo") or body.get("forget"):
                            return self._json(service.settings_undo(forget=bool(body.get("forget"))))
                        return self._json(service.settings_import(body.get("data")))
                    if route == "/api/report":      # 問題報告：先做出來給使用者看，他按了才存檔或複製；不會送到任何地方
                        if body.get("save"):
                            return self._json(service.report_save())
                        if body.get("copy"):
                            return self._json(service.report_copy())
                        return self._json(service.report_make(str(body.get("note") or ""), bool(body.get("settings", True)),
                                                              bool(body.get("log", True))))
                    if route == "/api/seen":      # 介面已經說過「更新好了」：不用再說
                        service.updated_from = ""
                        return self._json({"ok": True})
                    if route == "/api/storage":      # 搬大檔案、取消、刪掉沒在用的、打開資料夾
                        if body.get("choose"):      # 跳出選資料夾的視窗，會等使用者選完
                            return self._json(service.storage_choose())
                        if body.get("back"):
                            return self._json(service.storage_move(back=True))
                        if body.get("move"):
                            return self._json(service.storage_move(str(body["move"])))
                        if body.get("cancel"):
                            return self._json(service.storage_cancel())
                        if body.get("remove"):
                            return self._json(service.storage_remove(str(body["remove"])))
                        if body.get("open"):
                            return self._json(service.storage_open())
                        return self._json({"ok": False})
                    if route == "/api/usage":
                        return self._json(service.usage_clear() if body.get("clear") else {"ok": False})
                    if route == "/api/history":
                        if body.get("clear"):
                            return self._json(service.history_clear())
                        if body.get("export"):
                            return self._json(service.history_export(str(body.get("q") or "")))
                        return self._json({"ok": False})
                    if route == "/api/hotkeys":      # 設定頁在等使用者按新的組合：先停用，按完恢復
                        service.pause_hotkeys(bool(body.get("pause")))
                        return self._json({"ok": True, **service.hotkeys_state()})
                    if route == "/api/quick_input":      # 設定頁的「現在試試看」：跟按快速鍵叫出輸入框一樣
                        return self._json({"ok": service.quick_input()})
                    if route == "/api/clear":
                        service.clear_messages()
                        return self._json({"ok": True})
                    if route == "/api/record":
                        if "on" in body:
                            return self._json({"ok": True, **service.set_recording(bool(body.get("on")))})
                        if body.get("open"):
                            return self._json({"ok": service.open_transcript(str(body.get("open")))})
                        if body.get("choose"):
                            threading.Thread(target=service.choose_transcript_dir, daemon=True).start()
                            return self._json({"ok": True})
                        return self._json({"ok": True, **service.record_state()})
                    if route == "/api/exit":
                        self._json({"ok": True})
                        threading.Thread(target=service.on_quit, daemon=True).start()
                        return None
                    if route == "/api/chrome":
                        return self._json({"ok": service.set_chrome(str(body.get("caption")), str(body.get("text")), bool(body.get("dark")))})
                    if route == "/api/view":
                        service.set_view(str(body.get("view")))
                        return self._json({"ok": True})
                    if route == "/api/overlay":
                        if "edit" in body:
                            service.overlay.set_edit(bool(body.get("edit")))
                        if body.get("reset"):
                            service.overlay.reset_position()
                        if body.get("preset"):      # 一鍵版面：外觀和位置一起換
                            result = service.overlay.apply_preset(str(body.get("preset")))
                            return self._json({"ok": result is not None, **(result or {}), **service.overlay.state()})
                        if body.get("reset_look"):      # 恢復預設外觀（位置不動）
                            result = service.update_config({k: DEFAULTS[k] for k in OVERLAY_LOOK})
                            return self._json({"ok": True, **result, **service.overlay.state()})
                        if "rect" in body:      # 設定頁的螢幕圖上拖方框（final：放開了，這才存）
                            return self._json({**service.overlay.place(body.get("rect"), bool(body.get("final", True))),
                                               **{k: v for k, v in service.overlay.state().items() if k != "rect"}})
                        if "monitor" in body:      # 換一個螢幕顯示
                            return self._json({**service.overlay.choose_monitor(body.get("monitor")), "editing": service.overlay.editing})
                        if "layout_save" in body:      # 自己存的版面：存、套用、刪
                            return self._json(service.overlay.save_layout(body.get("layout_save")))
                        if "layout" in body:
                            return self._json(service.overlay.apply_layout(body.get("layout")))
                        if "layout_remove" in body:
                            return self._json(service.overlay.remove_layout(body.get("layout_remove")))
                        if body.get("test"):
                            threading.Thread(target=service.overlay.test, daemon=True).start()
                        return self._json({"ok": True, **service.overlay.state()})
                    if route == "/api/focus":
                        service.focus()
                        return self._json({"ok": True})
                    if route == "/api/update":
                        if body.get("rollback"):      # 回上一版
                            return self._json(service.rollback())
                        if body.get("unskip"):      # 退回之後不再問的那一版：恢復提示
                            return self._json(service.update_unskip())
                        service.update_choice(str(body.get("choice") or ""))
                        return self._json({"ok": True})
                    if route == "/api/retry":
                        threading.Thread(target=service._prepare, daemon=True).start()
                        return self._json({"ok": True})
                    if route == "/api/quit":
                        self._json({"ok": True})
                        threading.Thread(target=service.on_quit, daemon=True).start()
                        return None
                except Exception as ex:  # noqa: BLE001
                    service.log(f"api {route} failed: {ex!r}")
                    return self._json({"ok": False, "error": str(ex)}, 500)
                return self._json({"error": "not found"}, 404)

        return Handler


def ui_files() -> list[Path]:
    return sorted(p for p in UI_DIR.rglob("*") if p.is_file())
