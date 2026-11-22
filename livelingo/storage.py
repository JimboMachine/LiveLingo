"""
儲存空間：大檔案（翻譯模型、語音模型、翻譯引擎）放在哪、各佔多少，搬到別的磁碟，刪掉沒在用的。

設定、詞彙表、歷史和紀錄檔很小，永遠留在原本的資料夾；會搬的只有 models 和 engine 這兩個資料夾。
搬到別的地方時一定放在一個叫 LiveLingo 的資料夾裡，裡面放一個記號檔，卸載的時候看到記號才刪，不會刪到別人的東西。
"""

import os
import shutil
from pathlib import Path

from . import assets

MARK = ".livelingo-files"          # 記號檔：這個資料夾是 LiveLingo 放大檔案的地方
PARTS = ("models", "engine")       # 會搬的兩個資料夾
FOLDER = "LiveLingo"               # 使用者選的資料夾底下再開這一層
CHUNK = 4 * 1024 * 1024
SPARE_MB = 200                     # 搬過去之後那個磁碟至少還要剩這麼多


class Cancelled(Exception):
    pass


def store_dir(data: Path, saved: str) -> Path:
    """大檔案現在放在哪。設定裡記的資料夾不見了（例如外接硬碟沒插）就回到原本的資料夾。"""
    if saved:
        p = Path(saved)
        if p.is_dir() and (p / MARK).exists():
            return p
    return data


def target_for(picked: Path) -> Path:
    """使用者選了一個資料夾：實際放在裡面的 LiveLingo 資料夾（他選的本來就是我們的資料夾就直接用）。"""
    if picked.name.lower() == FOLDER.lower() and ((picked / MARK).exists() or not picked.exists() or not any(picked.iterdir())):
        return picked
    return picked / FOLDER


def size_bytes(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def free_bytes(folder: Path) -> int:
    """這個資料夾所在的磁碟還剩多少。資料夾還不存在就往上找存在的那一層。"""
    p = folder
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def _same_place(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def check_target(store: Path, target: Path) -> tuple[str, dict]:
    """可不可以搬到 target。可以回傳 ("", {})；不行回傳（原因代碼, 給介面填進句子的數字）。"""
    if _same_place(store, target):
        return "store_same", {}
    if any(_inside(target, store / part) for part in PARTS):
        return "store_inside", {}
    need = sum(size_bytes(store / part) for part in PARTS)
    free = free_bytes(target)
    if free < need + SPARE_MB * 1024 * 1024:
        return "store_no_space", {"need": round(need / 1e9, 1), "free": round(free / 1e9, 1)}
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".livelingo-write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        return "store_not_writable", {}
    return "", {}


def copy_parts(store: Path, target: Path, progress=None, cancel=None, mark: bool = True) -> None:
    """把 models 和 engine 複製到 target（原本的先不動，全部複製完、大小都對了才算數）。
    progress(已複製位元組, 總位元組)。cancel() 回傳 True 就停下來，把複製到一半的清掉。mark：放記號檔（搬回原本的資料夾不用）。"""
    files = []
    for part in PARTS:
        for root, _dirs, names in os.walk(store / part):
            for name in names:
                src = Path(root) / name
                if name.endswith(".part"):      # 沒下載完的不搬，之後會重新下載
                    continue
                files.append((src, target / src.relative_to(store)))
    total = sum(size_bytes(src) for src, _ in files) or 1
    done = 0
    try:
        target.mkdir(parents=True, exist_ok=True)
        if mark:
            (target / MARK).write_text("LiveLingo keeps its large files (models, engine) in this folder.\n", encoding="utf-8")
        for src, dst in files:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(src, "rb") as fin, open(dst, "wb") as fout:
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    block = fin.read(CHUNK)
                    if not block:
                        break
                    fout.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            if dst.stat().st_size != src.stat().st_size:
                raise OSError(f"copy of {src.name} is incomplete")
    except BaseException:
        remove_parts(target)
        raise


def remove_parts(folder: Path) -> list[str]:
    """把 folder 裡的 models、engine 和記號檔刪掉；資料夾空了就連資料夾一起刪。回傳刪不掉的（正在使用中）。"""
    left = []
    for part in PARTS:
        p = folder / part
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            if p.exists():
                left.append(str(p))
    if not left:
        try:
            (folder / MARK).unlink(missing_ok=True)
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass
    return left


def remove_moved(data: Path, saved: str) -> bool:
    """卸載時用：大檔案搬到別的地方的話，把那邊的也刪掉。只刪有記號檔的資料夾。回傳有沒有刪。"""
    if not saved:
        return False
    p = Path(saved)
    if _same_place(p, data) or not (p / MARK).exists():
        return False
    remove_parts(p)
    return True


def items(data: Path, store: Path, used: dict) -> list[dict]:
    """每一樣東西佔多少。used：{"models": 現在要用的模型代碼, "engines": 要用的引擎種類, "cudnn": 語音辨識有沒有在用顯示卡}。
    每一項 {"id", "bytes", "used", "removable"}；不存在的不列。"""
    rows = []

    def add(item_id: str, path: Path, in_use: bool, removable: bool | None = None) -> None:
        size = size_bytes(path) if path.exists() else 0
        if size:
            rows.append({"id": item_id, "bytes": size, "used": in_use,
                         "removable": (not in_use) if removable is None else removable})

    for model in assets.MODELS:
        add("model_" + model, assets.model_path(store, model), model in used.get("models", ()))
    for kind in assets.ENGINE_MB:
        add("engine_" + kind, assets.engine_dir(store, kind), kind in used.get("engines", ()))
    add("cudnn", assets.cudnn_dir(store), bool(used.get("cudnn")))
    add("whisper", store / "models" / "whisper", True)
    add("ocr", store / "models" / "ocr", True)
    part = sum(p.stat().st_size for p in _partials(store))
    if part:
        rows.append({"id": "partial", "bytes": part, "used": False, "removable": True})
    add("updates", data / "update", bool(used.get("updating")))
    small = sum(size_bytes(p) for p in data.iterdir() if p.name not in (*PARTS, "update")) if data.is_dir() else 0
    rows.append({"id": "records", "bytes": small, "used": True, "removable": False})
    return rows


def _partials(store: Path) -> list[Path]:
    out = []
    for part in PARTS:
        if (store / part).is_dir():
            out += [p for p in (store / part).rglob("*.part") if p.is_file()]
    return out


def remove_item(data: Path, store: Path, item_id: str) -> bool:
    """刪掉一樣沒在用的東西（要不要刪由呼叫的人先確認）。回傳有沒有刪乾淨。"""
    if item_id.startswith("model_") and item_id[6:] in assets.MODELS:
        paths = [assets.model_path(store, item_id[6:])]
    elif item_id.startswith("engine_") and item_id[7:] in assets.ENGINE_MB:
        paths = [assets.engine_dir(store, item_id[7:])]
    elif item_id == "cudnn":
        paths = [assets.cudnn_dir(store)]
    elif item_id == "updates":
        paths = [data / "update"]
    elif item_id == "partial":
        paths = _partials(store)
    else:
        return False
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    return not any(p.exists() for p in paths)
