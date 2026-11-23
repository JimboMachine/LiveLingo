"""
在 GitHub 的 Windows 機器上，把剛做好的安裝檔真的裝一次、更新一次、卸載一次，確認：
  1. 安靜安裝後程式在、桌面和開始功能表捷徑在、「新增或移除程式」裡有登記
  2. 裝好的程式自我檢查通過（視窗開得起來）
  3. 更新：假裝 GitHub 上有 99.0.0，程式會先問（問的時候帶著這一版改了什麼、要下載多大）；按「這次先不要」照常使用，按「現在更新」會自己下載、安裝、重新開啟，而且不會無限重裝
  3c. 回上一版：用留著的安裝檔退一次（核對雜湊、自己關掉、安靜地裝、再打開）
  3d. 程式檔還被佔著時更新：等它放開再裝；一直放不開就不裝；不管哪種，程式都打得開
  4a. 卸載時選擇留下設定（/KEEPDATA=1）：程式、捷徑、模型、引擎都刪掉，設定和詞彙表留著，之後再裝一次
  4b. 卸載後程式、捷徑、資料都清乾淨；大檔案搬到別的資料夾的話那邊也清掉，但不動別人的檔案
用法：python packaging/ci_install_test.py dist\\LiveLingo-Setup.exe
"""

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import winreg
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(errors="replace")      # 主控台是 cp1252：記錄檔裡的中文印不出來時用問號代替，不要整個中斷

SETUP = Path(sys.argv[1]).resolve()
LOCAL = Path(os.environ["LOCALAPPDATA"])
APP_DIR = LOCAL / "Programs" / "LiveLingo"
APP = APP_DIR / "LiveLingo.exe"
DATA = LOCAL / "LiveLingo"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B1F0E52-7C0D-4B7A-9E5B-2D7C5A3F1E90}_is1"
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]


def known_folder(guid: str) -> Path:
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(g))
    out = ctypes.c_wchar_p()
    ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
    return Path(out.value)


DESKTOP = known_folder("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}") / "LiveLingo.lnk"
START = known_folder("{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}") / "LiveLingo.lnk"


def step(text: str) -> None:
    print(f"\n=== {text}", flush=True)


LOGS: list[Path] = []


def fail(text: str) -> None:
    for path in LOGS:
        print(f"\n----- {path}")
        print(path.read_text(encoding="utf-8", errors="replace")[-6000:] if path.exists() else "(not written)")
    print("LiveLingo processes:", running())
    print(f"FAILED: {text}", flush=True)
    sys.exit(1)


def running() -> list[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq LiveLingo.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"LiveLingo.exe"')]


def wait_for(what: str, fn, seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(1)
    fail(f"timed out waiting for: {what}")


def registered() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None


def selftest(label: str) -> None:
    out = Path(tempfile.mkdtemp()) / "selftest.json"
    proc = subprocess.run([str(APP), "--selftest", str(out)], timeout=600)
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"ok": False, "failed": ["no output"]}
    print(json.dumps(result, ensure_ascii=True, indent=2)[:6000])      # 主控台是 cp1252，中文印不出來會整個中斷
    if proc.returncode != 0 or not result["ok"]:
        fail(f"{label}: selftest failed: {result.get('failed')}")


# ---------------------------------------------------------------- 1. 安裝
step("silent install")
log = Path(tempfile.gettempdir()) / "livelingo-install.log"
code = subprocess.run([str(SETUP), *SILENT, f"/LOG={log}"], timeout=900).returncode
if code != 0:
    print(log.read_text(errors="replace")[-4000:] if log.exists() else "(no log)")
    fail(f"installer exit code {code}")
for path in (APP, DESKTOP, START, APP_DIR / "unins000.exe"):
    print(f"{path}: {'ok' if path.exists() else 'MISSING'}")
    if not path.exists():
        fail(f"{path} was not created")
version = registered()
print("registered version:", version)
if not version:
    fail("no uninstall entry in the registry")
if running():
    fail("the app was started by a plain silent install")

# ---------------------------------------------------------------- 2. 裝好的程式自我檢查
step("installed app selftest")
selftest("installed app")

# ---------------------------------------------------------------- 3. 更新
step("update: ask, skip, then install")
digest = hashlib.sha256(SETUP.read_bytes()).hexdigest()


class Release(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        Release.hits.append(self.path)
        if self.path.endswith(".exe"):
            size = SETUP.stat().st_size
            start = 0
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start = int(rng[6:].split("-")[0])
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(size - start))
            self.end_headers()
            with open(SETUP, "rb") as f:
                f.seek(start)
                while chunk := f.read(1 << 20):
                    self.wfile.write(chunk)
            return
        if self.path.endswith("changes.json"):      # 跟安裝檔一起發行的「這一版改了什麼」
            body = json.dumps({"99.0.0": {"en": ["A change made up for this test"], "zh-TW": ["測試用的改動"]},
                               "0.0.1": {"en": ["Too old to be shown"]}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        tag = "v0.9.0" if "/tags/" in self.path else "v99.0.0"      # /tags/…：回上一版時查的那一版；其他：最新版
        body = json.dumps({"tag_name": tag, "assets": [
            {"name": "LiveLingo-Setup.exe", "size": SETUP.stat().st_size, "digest": "sha256:" + digest,
             "browser_download_url": f"{base}/LiveLingo-Setup.exe"},
            {"name": "changes.json", "size": 100, "browser_download_url": f"{base}/changes.json"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Release)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
data = Path(tempfile.mkdtemp(prefix="livelingo-update-"))
env = dict(os.environ, LIVELINGO_UPDATE_API=f"http://127.0.0.1:{httpd.server_address[1]}/latest", LIVELINGO_DATA=str(data))
(APP_DIR / "ci-marker.txt").write_text("old install")       # 更新會把程式檔換掉，但不該動到這種不認識的檔案以外的東西
LOGS.extend([data / "app.log", data / "update" / "install.log"])


def api(run: dict, route: str, body: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{run['port']}{route}?t={run['token']}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def run_info_of(folder: Path):
    try:
        return json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def asking():
    """程式停在「有新版本，要不要更新？」那個畫面時，回傳連到它的資訊；還沒到就回傳 None。"""
    run = run_info_of(data)
    if not run:
        return None
    try:
        state = api(run, "/api/state")
    except (OSError, ValueError):
        return None
    return run if state["phase"] == "updating" and (state.get("detail") or {}).get("step") == "ask" else None


# 3a. 有新版本時先問；按「這次先不要」要能照常使用，而且不會下載
skipper = subprocess.Popen([str(APP)], env=env)
print("started pid", skipper.pid)
run = wait_for("the app to ask about the update", asking, 180)
asked = api(run, "/api/state")["detail"]
print("update prompt:", json.dumps(asked, ensure_ascii=True))
if [n["version"] for n in asked.get("notes") or []] != ["99.0.0"] or asked["notes"][0]["items"].get("en") != ["A change made up for this test"]:
    fail("the update prompt does not carry what changed in the new version")
if asked.get("size") != SETUP.stat().st_size:
    fail("the update prompt does not say how large the download is")
api(run, "/api/update", {"choice": "later"})
wait_for("the app to carry on after 'not this time'", lambda: api(run, "/api/state")["phase"] != "updating", 60)
if any(h.endswith(".exe") for h in Release.hits):
    fail("the update was downloaded although the user said not this time")
print("skipped: phase is", api(run, "/api/state")["phase"], "requests:", Release.hits)
api(run, "/api/quit", {})
wait_for("the app to quit after skipping the update", lambda: skipper.poll() is not None, 60)
wait_for("no LiveLingo left running", lambda: not running(), 60)

# 3b. 按「現在更新」：下載、安裝、自己重新開啟
first = subprocess.Popen([str(APP)], env=env)
print("started pid", first.pid)
run = wait_for("the app to ask about the update again", asking, 180)
api(run, "/api/update", {"choice": "now"})
wait_for("the app to exit so the installer can run", lambda: first.poll() is not None, 300)
print("app exited with", first.returncode, "after requests:", Release.hits)
if first.returncode != 0:
    fail("the app did not close by itself for the update (it was killed)")
if not any(h.endswith(".exe") for h in Release.hits):
    fail("the app exited without downloading the update")
attempt = json.loads((data / "update.json").read_text(encoding="utf-8"))
print("update attempt:", attempt)
if attempt.get("version") != "99.0.0":
    fail("the update attempt was not recorded")
pids = wait_for("the installer to reopen the app", lambda: [p for p in running() if p != first.pid], 600)
print("app reopened with pid", pids)
run_info = wait_for("the reopened app to be ready", lambda: (data / "run.json").exists() and json.loads(
    (data / "run.json").read_text(encoding="utf-8")), 120)
time.sleep(8)       # 讓它有時間再檢查一次更新：應該看到「剛試過 99.0.0」而不是再裝一次
base = f"http://127.0.0.1:{run_info['port']}"
with urllib.request.urlopen(f"{base}/api/state?t={run_info['token']}", timeout=20) as r:
    state = json.loads(r.read().decode("utf-8"))
print("reopened app phase:", state["phase"], "version:", state["version"])
if state["phase"] == "updating":
    fail("the app is trying to update again (update loop)")
exe_requests = [h for h in Release.hits if h.endswith(".exe")]
if len(exe_requests) != 1:
    fail(f"the installer was downloaded {len(exe_requests)} times")
req = urllib.request.Request(f"{base}/api/quit?t={run_info['token']}", data=b"{}", headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=20)
wait_for("the app to quit when asked", lambda: not running(), 60)
print("app log:\n" + ((data / "app.log").read_text(encoding="utf-8", errors="replace")[-2000:] if (data / "app.log").exists() else "(empty)"))

# 3c. 回上一版：重新下載上一版的安裝檔（用過的安裝檔不留著），裝好自己重新開啟。下載到的其實是同一版的安裝檔
#     （手上只有這一個），看的是整條路走不走得通：查那一版、下載、核對雜湊、程式自己關掉、安靜地裝、再打開、
#     記下不要再問的版本。順便看用過的安裝檔有沒有被刪掉：一開程式就刪舊的，剛跑完的那一個等安裝程式結束再刪。
step("roll back by downloading the previous installer; used installers are removed")
used = data / "update" / "LiveLingo-Setup-0.9.0.exe"       # 回上一版時會下載成這個名字
stale = data / "update" / "LiveLingo-Setup-0.0.9.exe"      # 假裝是更早以前留下來的
stale.parent.mkdir(parents=True, exist_ok=True)
stale.write_bytes(b"x" * 4096)
(data / "update.json").write_text(json.dumps({"version": state["version"], "at": time.time(), "from": "0.9.0", "told": True}),
                                  encoding="utf-8")
(data / "run.json").unlink(missing_ok=True)
Release.hits.clear()
env_back = {k: v for k, v in env.items() if k != "LIVELINGO_UPDATE_API"}
env_back.update(LIVELINGO_NO_UPDATE="1", LIVELINGO_ROLLBACK_FLOOR="0.0.1",
                LIVELINGO_RELEASE_API=f"http://127.0.0.1:{httpd.server_address[1]}/tags/v{{version}}")
third = subprocess.Popen([str(APP)], env=env_back)
print("started pid", third.pid)


def settled():
    run = run_info_of(data)
    try:
        return run if run and api(run, "/api/state")["phase"] not in ("starting", "updating", "loading") else None
    except (OSError, ValueError):
        return None


run = wait_for("the app to be ready for rolling back", settled, 240)
offered = api(run, "/api/state")["update"]
print("roll back offered:", offered)
if not offered.get("previous") or offered["previous"].get("version") != "0.9.0":
    fail("rolling back to the previous version was not offered")
if stale.exists():
    fail("an old installer was still in the update folder after the app started")
answer = api(run, "/api/update", {"rollback": True})
print("roll back answer:", answer)
if not answer.get("ok"):
    fail("the app refused to roll back")
wait_for("the app to exit so the previous installer can run", lambda: third.poll() is not None, 300)
if third.returncode != 0:
    fail("the app did not close by itself for the roll back (it was killed)")
record = json.loads((data / "update.json").read_text(encoding="utf-8"))
print("roll back record:", record)
if record.get("version") != "0.9.0" or record.get("skip") != state["version"] or record.get("rollback") is not True:
    fail("the roll back was not recorded")
(data / "run.json").unlink(missing_ok=True)
pids = wait_for("the installer to reopen the app after rolling back", lambda: [p for p in running() if p != third.pid], 600)
run = wait_for("the reopened app to answer", settled, 240)
print("reopened after roll back, pid", pids, "phase", api(run, "/api/state")["phase"])
if "/tags/v0.9.0" not in Release.hits or len([h for h in Release.hits if h.endswith(".exe")]) != 1:
    fail(f"the previous installer was not downloaded exactly once: {Release.hits}")
wait_for("the used installer to be removed once the installer has finished", lambda: not used.exists(), 150)
print("used installer removed; update folder now holds:", sorted(p.name for p in used.parent.iterdir()))
httpd.shutdown()
api(run, "/api/quit", {})
wait_for("the app to quit after the roll back check", lambda: not running(), 60)

# ---------------------------------------------------------------- 3d. 更新時程式檔還被佔著
# 2.4.2 那次：LiveLingo 放掉「我還開著」的記號以後還沒真的結束，安裝程式先刪舊檔、換檔失敗、中止，
# 留下一個沒有 python312.dll、打不開的程式。現在不管怎樣，安裝程式跑完之後程式都要打得開。
step("update while program files are still in use")
HELD = APP_DIR / "_internal" / "python312.dll"


def update_while_held(seconds: float) -> int:
    """佔住 python312.dll 幾秒（像一個還在結束的 LiveLingo），同時跑更新，回傳安裝程式的結束碼。"""
    if not HELD.exists():
        fail(f"{HELD} is missing before the test")
    handle = open(HELD, "rb")       # Python 開檔不給 FILE_SHARE_DELETE：刪不掉，資料夾也改不了名
    threading.Timer(seconds, handle.close).start()
    held_log = Path(tempfile.gettempdir()) / f"livelingo-held-{int(seconds)}.log"
    LOGS.append(held_log)
    started = time.time()
    code = subprocess.run([str(SETUP), *SILENT, f"/LOG={held_log}"], timeout=900).returncode
    print(f"held {seconds:.0f}s: installer exit code {code} after {time.time() - started:.0f}s")
    for line in held_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "LiveLingo:" in line or "DeleteFile" in line or "Rolling back" in line:
            print("   ", line.strip())
    time.sleep(max(0.0, seconds - (time.time() - started)) + 1)      # 佔住的檔案放開了再檢查
    if not HELD.exists() or not APP.exists():
        fail(f"held {seconds:.0f}s: the program was left without {HELD.name if not HELD.exists() else APP.name}")
    selftest(f"after an update with files held for {seconds:.0f}s")
    return code


# 佔幾秒就放開：安裝程式要等它，然後正常裝好
code = update_while_held(8)
if code != 0:
    fail("the installer did not wait for the program files to be free")
# 一直佔著（比安裝程式願意等的久）：可以不更新，但程式不能壞
code = update_while_held(100)
print("long hold:", "installed anyway" if code == 0 else "nothing was changed")
if running():
    fail("a plain silent install started the app")
if (APP_DIR / "_internal.old").exists() and code != 0:
    fail("the old program files were left as _internal.old after a failed update")

# ---------------------------------------------------------------- 4a. 卸載，但把自己的設定留下來
step("uninstall, keeping settings and history")
DATA.mkdir(parents=True, exist_ok=True)
for name in ("models", "engine", "update", "webview"):
    (DATA / name).mkdir(exist_ok=True)
    (DATA / name / "big-file.bin").write_bytes(b"x" * 1000)
(DATA / "glossary-kept-by-test.json").write_text("{}", encoding="utf-8")
KEPT_OTHER = Path(tempfile.mkdtemp(prefix="ll-other-drive-"))
KEPT_MOVED = KEPT_OTHER / "LiveLingo"
(KEPT_MOVED / "models").mkdir(parents=True)
(KEPT_MOVED / "models" / "moved-model.bin").write_bytes(b"x" * 1000)
(KEPT_MOVED / ".livelingo-files").write_text("LiveLingo\n", encoding="utf-8")
saved = json.loads((DATA / "config.json").read_text(encoding="utf-8")) if (DATA / "config.json").exists() else {}
saved.update(store_dir=str(KEPT_MOVED), my_lang="zh-TW")
(DATA / "config.json").write_text(json.dumps(saved), encoding="utf-8")
code = subprocess.run([str(APP_DIR / "unins000.exe"), *SILENT, "/KEEPDATA=1"], timeout=600).returncode
print("uninstaller exit code", code)
wait_for("the program folder to be removed", lambda: not APP.exists() and not (APP_DIR / "_internal").exists(), 180)
# 資料夾是卸載的最後一步才刪的（程式檔刪完之後），所以要再等一下
wait_for("the large files to be removed", lambda: not any((DATA / n).exists() for n in ("models", "engine", "update", "webview")), 120)
for path in (DESKTOP, START, KEPT_MOVED, DATA / "models", DATA / "engine", DATA / "update", DATA / "webview"):
    print(f"{path}: {'still there' if path.exists() else 'removed'}")
    if path.exists():
        fail(f"{path} was left behind after uninstalling")
for path in (DATA / "config.json", DATA / "glossary-kept-by-test.json"):
    print(f"{path}: {'kept' if path.exists() else 'GONE'}")
    if not path.exists():
        fail(f"{path} was removed although the user asked to keep their data")
kept = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
if kept.get("my_lang") != "zh-TW" or kept.get("store_dir"):
    fail(f"the kept settings are wrong: my_lang={kept.get('my_lang')!r} store_dir={kept.get('store_dir')!r}")
if registered():
    fail("the uninstall entry is still in the registry")
print("program and large files removed; settings kept and no longer pointing at the removed folder")

step("install again over the kept settings")
code = subprocess.run([str(SETUP), *SILENT, f"/LOG={log}"], timeout=900).returncode
if code != 0 or not APP.exists() or not registered():
    print(log.read_text(errors="replace")[-4000:] if log.exists() else "(no log)")
    fail(f"installing again failed (exit code {code})")
if running():
    fail("the app was started by a plain silent install")

# ---------------------------------------------------------------- 4b. 卸載，全部刪掉
step("uninstall, removing everything")
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "models").mkdir(exist_ok=True)
(DATA / "models" / "fake-model.bin").write_bytes(b"x" * 1000)
# 大檔案搬到別的地方的情況：那邊的也要刪掉，但是同一個資料夾裡別人的東西不能動
OTHER = Path(tempfile.mkdtemp(prefix="ll-other-drive-"))
MOVED = OTHER / "LiveLingo"
(MOVED / "models").mkdir(parents=True)
(MOVED / "engine" / "cpu").mkdir(parents=True)
(MOVED / "models" / "moved-model.bin").write_bytes(b"x" * 1000)
(MOVED / "engine" / "cpu" / "moved-engine.dll").write_bytes(b"x" * 1000)
(MOVED / ".livelingo-files").write_text("LiveLingo\n", encoding="utf-8")
(OTHER / "not-ours.txt").write_text("someone else's file\n", encoding="utf-8")
saved = json.loads((DATA / "config.json").read_text(encoding="utf-8")) if (DATA / "config.json").exists() else {}
saved["store_dir"] = str(MOVED)
(DATA / "config.json").write_text(json.dumps(saved), encoding="utf-8")
code = subprocess.run([str(APP_DIR / "unins000.exe"), *SILENT], timeout=600).returncode
print("uninstaller exit code", code)
wait_for("the program folder to be removed", lambda: not APP.exists() and not (APP_DIR / "_internal").exists(), 180)
wait_for("the data folder to be removed", lambda: not DATA.exists(), 120)
for path in (DESKTOP, START, DATA, MOVED):
    print(f"{path}: {'still there' if path.exists() else 'removed'}")
    if path.exists():
        fail(f"{path} was left behind after uninstalling")
if not (OTHER / "not-ours.txt").exists():
    fail("uninstalling removed a file that is not LiveLingo's")
print("moved large files removed; the neighbouring file was left alone")
if registered():
    fail("the uninstall entry is still in the registry")
print("\nALL INSTALL / UPDATE / UNINSTALL CHECKS PASSED", flush=True)
