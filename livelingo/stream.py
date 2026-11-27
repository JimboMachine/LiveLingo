"""
直播模式：給 OBS 的「瀏覽器來源」一個乾淨的雙語字幕頁。

- 網址固定：連接埠固定（stream_port，被佔用時往後找），通行碼第一次打開時產生、存在設定裡，重開 LiveLingo 網址不變，
  OBS 裡貼一次就好。
- 只開在這台電腦上（127.0.0.1），還要對得上通行碼和 Host，別的電腦、別的網頁都碰不到。
- 這一頁只有字幕：背景透明，沒有按鈕，也拿不到設定或任何別的東西。
- 跟主視窗的事件分開送（主視窗靠「有沒有介面連著」判斷視窗開好了沒，OBS 連進來不能算數）。
"""

import json
import queue
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT_TRIES = 10          # 設定的連接埠被佔用時，往後再試幾個
KEEP = 6                 # OBS 剛連上時，補給它最近幾則（不然要等下一句才有字）
SECONDS = 15             # 一則字幕停留幾秒（浮動留言設成「不會消失」時用這個）

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LiveLingo</title>
<style>
html,body{margin:0;padding:0;background:transparent;overflow:hidden}
body{font-family:"Microsoft JhengHei","Microsoft YaHei","Malgun Gothic","Yu Gothic UI","Segoe UI",sans-serif;
  color:#fff;display:flex;flex-direction:column;justify-content:flex-end;height:100vh;box-sizing:border-box;padding:12px 18px}
.line{margin:6px 0;transition:opacity .4s;text-shadow:0 0 3px #000,0 0 3px #000,0 2px 4px #000;text-align:center}
.line.out{opacity:0}
.orig{font-size:calc(var(--size) * .62);opacity:.85}
.tr{font-size:var(--size);font-weight:700;line-height:1.25}
.me .tr{color:#9fe7ff}
.who{font-size:calc(var(--size) * .55);opacity:.8;margin-right:.4em}
html.wall,html.wall body{background:#0B0D10}
html.wall .line{text-align:left;text-shadow:none;border-bottom:1px solid #222;padding-bottom:8px}
</style></head>
<body style="--size:36px"><div id="lines"></div>
<script>
(function () {
  var params = new URLSearchParams(location.search);
  var key = params.get("k") || "";
  var wall = params.get("wall") === "1";      // 副螢幕字幕牆（2.16.0）：黑底、多幾行、不會自己消失
  if (wall) document.documentElement.classList.add("wall");
  var style = { original: true, seconds: 15, lines: 3, size: 36 };
  var items = [];
  var box = document.getElementById("lines");
  function el(tag, cls, text) { var e = document.createElement(tag); e.className = cls; e.textContent = text; return e; }
  function draw() {
    var now = Date.now();
    items = items.filter(function (m) { return !style.seconds || now - m.at < style.seconds * 1000; });
    var show = items.slice(-style.lines);
    box.innerHTML = "";
    show.forEach(function (m) {
      var d = el("div", "line " + (m.who === "me" ? "me" : "them"), "");
      if (style.original && m.original && m.original !== m.text) d.appendChild(el("div", "orig", m.original));
      var tr = el("div", "tr", "");
      if (m.name) tr.appendChild(el("span", "who", m.name));
      tr.appendChild(document.createTextNode(m.text));
      d.appendChild(tr);
      if (style.seconds && now - m.at > style.seconds * 1000 - 400) d.classList.add("out");
      box.appendChild(d);
    });
  }
  function put(m) {
    for (var i = 0; i < items.length; i++) if (items[i].id === m.id) { m.at = items[i].at; items[i] = m; draw(); return; }
    m.at = Date.now(); items.push(m); items = items.slice(-20); draw();
  }
  function connect() {
    var es = new EventSource("/obs/events?k=" + encodeURIComponent(key));
    es.addEventListener("style", function (e) {
      style = JSON.parse(e.data);
      if (wall) { style.lines = Math.max(style.lines, 10); style.seconds = 0; style.original = true; }
      document.body.style.setProperty("--size", style.size + "px"); draw();
    });
    es.addEventListener("line", function (e) { put(JSON.parse(e.data)); });
    es.addEventListener("remove", function (e) { var id = JSON.parse(e.data).id; items = items.filter(function (m) { return m.id !== id; }); draw(); });
    es.addEventListener("clear", function () { items = []; draw(); });
    es.onerror = function () { es.close(); setTimeout(connect, 2000); };      // LiveLingo 重開了：自己再連
  }
  setInterval(draw, 500);
  connect();
})();
</script></body></html>
"""


class StreamOut:
    def __init__(self, service):
        self.service = service
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=KEEP)
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0
        self.error = ""
        self.wall_on = False      # 副螢幕字幕牆開著（2.16.0）：沒開直播模式也要讓這一頁開著

    # ---- 開關 ----

    def apply(self) -> None:
        """照設定開或關。打開時沒有通行碼就產生一個存起來（之後網址固定）。"""
        cfg = self.service.cfg
        if not cfg["stream"] and not self.wall_on:
            self.stop()
            return
        if not cfg["stream_key"]:
            cfg.set(stream_key=secrets.token_urlsafe(12))
        if self.httpd is None:
            self.start()
        self.send("style", self.style())

    def start(self) -> None:
        handler = self._handler()
        first = int(self.service.cfg["stream_port"] or 47860)
        self.error = ""
        for port in range(first, first + PORT_TRIES):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
            except OSError:
                continue
            httpd.daemon_threads = True
            self.httpd, self.port = httpd, httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True, name="stream").start()
            self.service.log(f"stream page on port {self.port}")
            return
        self.error = "port"
        self.service.log(f"stream page: ports {first}-{first + PORT_TRIES - 1} are all in use")

    def stop(self) -> None:
        httpd, self.httpd, self.port = self.httpd, None, 0
        if httpd is not None:
            self.send("bye", {})
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()

    def url(self, wall: bool = False) -> str:
        if not self.port:
            return ""
        return f"http://127.0.0.1:{self.port}/obs?k={self.service.cfg['stream_key']}" + ("&wall=1" if wall else "")

    def state(self) -> dict:
        with self._lock:
            viewers = len(self._subs)
        return {"on": bool(self.service.cfg["stream"]), "url": self.url(), "port": self.port,
                "wanted": int(self.service.cfg["stream_port"] or 0), "viewers": viewers, "error": self.error}

    def style(self) -> dict:
        cfg = self.service.cfg
        seconds = int(cfg["overlay_seconds"] or 0) or SECONDS
        return {"original": bool(cfg["stream_original"]), "seconds": seconds, "lines": 3,
                "size": max(20, min(72, int(round(float(cfg["overlay_font"] or 24) * 1.5))))}

    # ---- 字幕 ----

    def line_of(self, msg: dict, text: str | None = None) -> dict | None:
        """一則訊息在直播字幕上長什麼樣子；不該出現的（還沒翻好、失敗、關掉了「我的也顯示」）回傳 None。"""
        cfg = self.service.cfg
        if msg.get("who") == "me" and not cfg["stream_mine"]:
            return None
        if text is None:
            if msg.get("pending") or msg.get("error") or not msg.get("translated"):
                return None
            text = msg["translated"]
        name = ""
        tag = getattr(self.service, "speaker_tag", None)
        who = tag(msg) if tag else None
        if who:
            name = who[1] or f"#{who[0]}"
        return {"id": msg["id"], "who": msg.get("who", "them"), "text": str(text), "original": str(msg.get("original") or ""),
                "name": name}

    def on_message(self, msg: dict) -> None:
        if self.httpd is None:
            return
        line = self.line_of(msg)
        if line:
            self._remember(line)
            self.send("line", line)

    def on_partial(self, msg: dict, text: str) -> None:
        """對方還在說、譯文翻到一半：先顯示（跟浮動留言一樣，螢幕上讀到的要翻完才出）。"""
        if self.httpd is None or msg.get("via") != "heard" or not text.strip():
            return
        line = self.line_of(msg, text)
        if line:
            self.send("line", line)

    def on_removed(self, msg_id) -> None:
        if self.httpd is None:
            return
        with self._lock:
            for item in list(self._recent):
                if item["id"] == msg_id:
                    self._recent.remove(item)
        self.send("remove", {"id": msg_id})

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()
        if self.httpd is not None:
            self.send("clear", {})

    def _remember(self, line: dict) -> None:
        with self._lock:
            for i, item in enumerate(self._recent):
                if item["id"] == line["id"]:
                    self._recent[i] = dict(line, at=item["at"])
                    return
            self._recent.append(dict(line, at=time.time()))

    def send(self, event: str, data) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass

    # ---- 網頁 ----

    def _handler(self):
        out = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _ok(self, query: dict) -> bool:
                host = (self.headers.get("Host") or "").lower()
                if host not in (f"127.0.0.1:{out.port}", f"localhost:{out.port}"):
                    return False
                key = str(out.service.cfg["stream_key"] or "")
                return bool(key) and secrets.compare_digest((query.get("k") or [""])[0], key)

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):   # noqa: N802
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not self._ok(query):
                    return self._send(403, b"forbidden", "text/plain")
                if url.path == "/obs":
                    return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/obs/events":
                    return self._events()
                return self._send(404, b"not found", "text/plain")

            def _events(self) -> None:
                q: queue.Queue = queue.Queue(maxsize=500)
                with out._lock:
                    out._subs.add(q)
                    recent = [dict(item) for item in out._recent]
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    first = [("style", out.style())] + [("line", {k: v for k, v in item.items() if k != "at"})
                                                        for item in recent if time.time() - item["at"] < out.style()["seconds"]]
                    for event, data in first:
                        self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    out.service.hub.publish("stream", out.state())
                    while not out.service.closing.is_set() and out.httpd is not None:
                        try:
                            event, data = q.get(timeout=10)
                            if event == "bye":
                                break
                            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                        except queue.Empty:
                            chunk = ": ping\n\n"
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
                finally:
                    with out._lock:
                        out._subs.discard(q)
                    self.close_connection = True
                    out.service.hub.publish("stream", out.state())

        return Handler
