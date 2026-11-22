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
TAG_API = "https://api.github.com/repos/JimboMachine/LiveLingo/releases/tags/v{version}"      # 某一個舊版本（回上一版用）
ASSET_NAME = "LiveLingo-Setup.exe"
NOTES_NAME = "changes.json"   # 跟安裝檔一起發行的「每一版改了什麼」
NOTES_LIMIT = 200_000
# 最舊可以退回到哪一版。比這更舊的版本不認得之後才有的東西（搬過位置的大檔案、卸載時叫程式清東西的參數），
# 退回去會出事（要重新下載模型、卸載會卡住），所以不提供。之後新版如果又加了舊版不認得的東西，要把這個數字往上調。
ROLLBACK_FLOOR = "2.3.0"
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


def _installer(rel: dict) -> dict | None:
    """從 GitHub 的一筆發行資料裡找出安裝檔：{"version", "url", "size", "sha256", "notes_url"}。"""
    notes_url = next((a.get("browser_download_url") or "" for a in rel.get("assets", []) if a.get("name") == NOTES_NAME), "")
    for a in rel.get("assets", []):
        if a.get("name") == ASSET_NAME:
            digest = a.get("digest") or ""
            return {"version": ".".join(str(n) for n in parse_version(rel.get("tag_name") or "")), "url": a["browser_download_url"],
                    "size": int(a.get("size") or 0), "notes_url": notes_url,
                    "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""}
    return None


def _release(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "LiveLingo", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(timeout: float = 8) -> dict | None:
    """有新版本就回傳 {"version", "url", "size", "sha256", "notes_url"}，沒有或查不到就回傳 None。"""
    rel = _release(api_url(), timeout)
    if rel.get("draft") or rel.get("prerelease") or not is_newer(rel.get("tag_name") or ""):
        return None
    return _installer(rel)


def release_of(version: str, timeout: float = 8) -> dict | None:
    """某一個指定版本的安裝檔在哪（回上一版、安裝檔沒留著的時候用）。查不到會丟出例外或回傳 None。"""
    rel = _release((os.environ.get("LIVELINGO_RELEASE_API") or TAG_API).format(version=version), timeout)
    info = _installer(rel)
    return info if info and parse_version(info["version"]) == parse_version(version) else None


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
    """剛剛是不是自動更新（或退回）完第一次開：是的話回傳之前的版本號（只會說一次），不是回傳空字串。"""
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
    """記住上次更新（或退回）到哪一版、從哪一版來的。裝完還是舊版的話不要馬上再試。"""

    def __init__(self, folder: Path):
        self.path = folder / "update.json"

    def read(self) -> dict:
        try:
            last = json.loads(self.path.read_text(encoding="utf-8"))
            return last if isinstance(last, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def recently_tried(self, version: str) -> bool:
        last = self.read()
        try:
            return last.get("version") == version and time.time() - float(last.get("at", 0)) < RETRY_AFTER
        except (TypeError, ValueError):
            return False

    def record(self, version: str, **more) -> None:
        """more：skip（之後不要再問的版本）、rollback（這次是退回）、from_sha256（現在這一版安裝檔的雜湊，退回時核對）。"""
        try:
            self.path.write_text(json.dumps({"version": version, "at": time.time(), "from": __version__, **more}), encoding="utf-8")
        except OSError:
            pass


def installer_path(folder: Path, version: str) -> Path:
    return folder / f"LiveLingo-Setup-{version}.exe"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def previous(data: Path) -> dict | None:
    """可以退回去的上一版：{"version", "kept"（安裝檔還留著，不用再下載）, "bytes"}。
    沒有可以退的（沒有自動更新過、現在就是退回來的、或上一版太舊）回傳 None。"""
    last = Attempts(data).read()
    older = str(last.get("from") or "")
    floor = os.environ.get("LIVELINGO_ROLLBACK_FLOOR") or ROLLBACK_FLOOR      # 環境變數是給測試用的
    if last.get("version") != __version__ or not older or not is_newer(__version__, older) or is_newer(floor, older):
        return None
    path = kept_installer(data, older, last)
    return {"version": older, "kept": bool(path), "bytes": path.stat().st_size if path else 0}


def kept_installer(data: Path, version: str, last: dict | None = None) -> Path | None:
    """上一版的安裝檔還留著、而且跟當初下載的一模一樣（雜湊對得上）才回傳；不然回傳 None（要重新下載）。"""
    last = Attempts(data).read() if last is None else last
    path = installer_path(data / "update", version)
    want = str(last.get("from_sha256") or "")
    try:
        if last.get("from") == version and want and path.is_file() and sha256_of(path) == want:
            return path
    except OSError:
        pass
    return None


def skipped(data: Path) -> str:
    """退回上一版之後，不要再問的那個版本（有更新的版本出來才會再問）。沒有就回傳空字串。"""
    last = Attempts(data).read()
    return str(last.get("skip") or "") if last.get("version") == __version__ else ""


def unskip(data: Path) -> None:
    attempts = Attempts(data)
    last = attempts.read()
    if last.pop("skip", None):
        try:
            attempts.path.write_text(json.dumps(last), encoding="utf-8")
        except OSError:
            pass


def download(info: dict, folder: Path, progress=None, cancel=None) -> Path:
    """下載安裝檔並核對大小和雜湊，回傳檔案位置。"""
    folder.mkdir(parents=True, exist_ok=True)
    dest = installer_path(folder, info["version"])
    keep = installer_path(folder, __version__).name      # 現在這一版的安裝檔留著：更新之後想回上一版，不用再下載
    for old in folder.glob("LiveLingo-Setup-*"):
        if old.name not in (dest.name, dest.name + ".part", keep):
            try:
                old.unlink()
            except OSError:
                pass
    fetch(info["url"], dest, progress, cancel=cancel, attempts=3)      # cancel() 回傳 True 時中止（使用者按了取消）
    ok = not info.get("size") or dest.stat().st_size == info["size"]
    if ok and info.get("sha256"):
        ok = sha256_of(dest) == info["sha256"]
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
