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
                if length <= 0 or length > 200_000:
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
                        service.update_config(body)
                        return self._json({"ok": True, "config": service.cfg.snapshot()})
                    if route == "/api/source":
                        service.set_source(str(body.get("key")), bool(body.get("on")))
                        return self._json({"ok": True})
                    if route == "/api/send":
                        service.submit("typed", str(body.get("text") or "")[:500])
                        return self._json({"ok": True})
                    if route == "/api/region":
                        service.select_region(then_enable=bool(body.get("enable", True)))
                        return self._json({"ok": True})
                    if route == "/api/ocr_test":
                        return self._json(service.ocr_test())
                    if route == "/api/meters":
                        service.set_meters(bool(body.get("on")))
                        return self._json({"ok": True})
                    if route == "/api/download":
                        service.start_download()
                        return self._json({"ok": True})
                    if route == "/api/fix":
                        ok = service.fix(int(body.get("id") or 0), str(body.get("src") or ""), str(body.get("dst") or ""))
                        return self._json({"ok": ok})
                    if route == "/api/glossary/add":
                        return self._json({"ok": service.glossary_add(str(body.get("src") or ""), str(body.get("dst") or ""))})
                    if route == "/api/glossary/remove":
                        service.glossary_remove(str(body.get("src") or ""))
                        return self._json({"ok": True})
                    if route == "/api/copy":
                        return self._json({"ok": service.copy(int(body.get("id") or 0))})
                    if route == "/api/clear":
                        service.clear_messages()
                        return self._json({"ok": True})
                    if route == "/api/view":
                        service.set_view(str(body.get("view")))
                        return self._json({"ok": True})
                    if route == "/api/focus":
                        service.focus()
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
