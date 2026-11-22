"""
更新：每次開啟時到 GitHub 看有沒有新版本。有的話先問使用者，同意才下載新的安裝檔、安靜地裝好，再自動重開；
選「這次先不要」就照常使用，下次開啟時再問。
沒有網路或下載失敗時不會把人卡住，這次先照常使用，下次開啟再試。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__
from .assets import DownloadError, fetch

RELEASE_API = "https://api.github.com/repos/JimboMachine/LiveLingo/releases/latest"
ASSET_NAME = "LiveLingo-Setup.exe"
NOTES_NAME = "changes.json"   # 跟安裝檔一起發行的「每一版改了什麼」
NOTES_LIMIT = 200_000
RETRY_AFTER = 30 * 60     # 同一個版本裝了還是舊的話，隔這麼久才再試（避免一直重裝重開）


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def api_url() -> str:
    return os.environ.get("LIVELINGO_UPDATE_API") or RELEASE_API


def enabled() -> bool:
    """只有裝好的 Windows 版才自動更新（從原始碼執行時不會）。測試時可以用環境變數強制打開。"""
    if os.environ.get("LIVELINGO_NO_UPDATE"):
        return False
    return bool(os.environ.get("LIVELINGO_UPDATE_API")) or (sys.platform == "win32" and getattr(sys, "frozen", False))


def check(timeout: float = 8) -> dict | None:
    """有新版本就回傳 {"version", "url", "size", "sha256", "notes_url"}，沒有或查不到就回傳 None。"""
    req = urllib.request.Request(api_url(), headers={"User-Agent": "LiveLingo", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.loads(r.read().decode("utf-8"))
    tag = rel.get("tag_name") or ""
    if rel.get("draft") or rel.get("prerelease") or not is_newer(tag):
        return None
    notes_url = next((a.get("browser_download_url") or "" for a in rel.get("assets", []) if a.get("name") == NOTES_NAME), "")
    for a in rel.get("assets", []):
        if a.get("name") == ASSET_NAME:
            digest = a.get("digest") or ""
            return {"version": ".".join(str(n) for n in parse_version(tag)), "url": a["browser_download_url"],
                    "size": int(a.get("size") or 0), "notes_url": notes_url,
                    "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""}
    return None


def fetch_notes(url: str, timeout: float = 6) -> dict:
    """新版本改了什麼（跟安裝檔一起發行的 changes.json）。拿不到、太大、看不懂都回傳空的：沒有說明也照樣可以更新。"""
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LiveLingo"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(NOTES_LIMIT + 1)
        if len(raw) > NOTES_LIMIT:
            return {}
        from .changes import clean

        return clean(json.loads(raw.decode("utf-8")))
    except Exception:  # noqa: BLE001
        return {}


def just_updated(folder: Path) -> str:
    """剛剛是不是自動更新完第一次開：是的話回傳舊的版本號（只會說一次），不是回傳空字串。"""
    path = folder / "update.json"
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
        if last.get("version") != __version__ or last.get("told") or not last.get("from") or last["from"] == __version__:
            return ""
        last["told"] = True
        path.write_text(json.dumps(last), encoding="utf-8")
        return str(last["from"])
    except Exception:  # noqa: BLE001
        return ""


class Attempts:
    """記住上次試著更新到哪一版，裝完還是舊版的話不要馬上再試。"""

    def __init__(self, folder: Path):
        self.path = folder / "update.json"

    def recently_tried(self, version: str) -> bool:
        try:
            last = json.loads(self.path.read_text(encoding="utf-8"))
            return last.get("version") == version and time.time() - float(last.get("at", 0)) < RETRY_AFTER
        except Exception:  # noqa: BLE001
            return False

    def record(self, version: str) -> None:
        try:
            self.path.write_text(json.dumps({"version": version, "at": time.time(), "from": __version__}), encoding="utf-8")
        except OSError:
            pass


def download(info: dict, folder: Path, progress=None, cancel=None) -> Path:
    """下載安裝檔並核對大小和雜湊，回傳檔案位置。"""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"LiveLingo-Setup-{info['version']}.exe"
    for old in folder.glob("LiveLingo-Setup-*"):
        if old.name not in (dest.name, dest.name + ".part"):
            try:
                old.unlink()
            except OSError:
                pass
    fetch(info["url"], dest, progress, cancel=cancel, attempts=3)      # cancel() 回傳 True 時中止（使用者按了取消）
    ok = not info.get("size") or dest.stat().st_size == info["size"]
    if ok and info.get("sha256"):
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        ok = h.hexdigest() == info["sha256"]
    if not ok:
        dest.unlink(missing_ok=True)
        raise DownloadError("the downloaded installer did not match the published file")
    return dest


def launch_installer(path: Path) -> None:
    """安靜地執行安裝檔。安裝程式會等這個程式關掉、裝好之後自己把 LiveLingo 再打開。"""
    args = [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/UPDATE=1",
            f"/LOG={path.with_name('install.log')}"]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
