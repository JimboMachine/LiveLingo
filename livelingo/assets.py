"""
第一次使用時下載需要的檔案（存在使用者的資料夾，之後離線也能用）：
  翻譯引擎 llama.cpp、翻譯模型 TranslateGemma、螢幕文字辨識模型、語音辨識模型 Whisper。
下載到一半中斷的話，下次會從中斷的地方接著下載。
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UA = {"User-Agent": "LiveLingo"}
HF = "https://huggingface.co"

# 要下載的每個檔案都鎖在一個確切的版本上，而且記下它的大小和 sha256：下載完要核對，不一樣就不用。
# 這些檔案放在別人的帳號上；不鎖的話，對方換了檔案或出了新版，新安裝的人拿到的就不是我們測過的東西。
# 要換版本：改下面的版本代碼（Hugging Face 的 commit、llama.cpp 的 tag），再把新檔案的大小和 sha256 填進 CHECKS / ENGINE_FILES。


def _hf(repo: str, revision: str, path: str) -> str:
    return f"{HF}/{repo}/resolve/{revision}/{path}"


_GEMMA_4B = ("bullerwins/translategemma-4b-it-GGUF", "7c938465a870d8624bcfa98a8e4a3510053c19a8")
_GEMMA_12B = ("bullerwins/translategemma-12b-it-GGUF", "d7d1d8cc4ff53d4bc883ef33eae3894f07833b63")
_OCR = ("monkt/paddleocr-onnx", "7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65")
_WHISPER = ("deepdml/faster-whisper-large-v3-turbo-ct2", "4df90f75321148c3a29a9e2351b7ddf8f5b115a8")

MODELS = {
    "4b": (_hf(*_GEMMA_4B, "translategemma-4b-it-Q4_K_M.gguf"), "translategemma-4b.gguf", 2490),
    "12b": (_hf(*_GEMMA_12B, "translategemma-12b-it-Q4_K_M.gguf"), "translategemma-12b.gguf", 7300),
}
OCR_FILES = [
    (_hf(*_OCR, "detection/v3/det.onnx"), "ocr/det.onnx", 3),
    (_hf(*_OCR, "languages/korean/rec.onnx"), "ocr/korean_rec.onnx", 13),
    (_hf(*_OCR, "languages/korean/dict.txt"), "ocr/korean_dict.txt", 1),
]
WHISPER_FILES = [(_hf(*_WHISPER, n), f"whisper/{n}", mb) for n, mb in (
    ("config.json", 1), ("preprocessor_config.json", 1), ("tokenizer.json", 3), ("vocabulary.json", 1),
    ("model.bin", 1620))]
# 存在 models/ 下面的檔名 → (位元組數, sha256)。上面每一個檔案都要有（有測試在看）。
CHECKS = {
    "translategemma-4b.gguf": (2489909312, "7f7357c14abd9da4eb200b38b05da502cd6e10d7e1d403fbc9f78c19f3209b72"),
    "translategemma-12b.gguf": (7300793664, "9196d728812afbf5efc10b539298585725edc3a4ecc092c22fdde5bbaf41879e"),
    "ocr/det.onnx": (2429873, "ee40e80071ba3a320d4efda75f3e22047a7d049e9bf7bcaaf9daea23fc21b935"),
    "ocr/korean_rec.onnx": (13401252, "322f140154c820fcb83c3d24cfe42c9ec70dd1a1834163306a7338136e4f1eaa"),
    "ocr/korean_dict.txt": (47451, "a88071c68c01707489baa79ebe0405b7beb5cca229f4fc94cc3ef992328802d7"),
    "whisper/config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
    "whisper/preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
    "whisper/tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
    "whisper/vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    "whisper/model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
}

# 翻譯引擎（llama.cpp 的 llama-server）：鎖在這一個版本，不再去抓「當天最新的」。
ENGINE_TAG = "b12631"
ENGINE_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_TAG}"
# 哪一種引擎要哪幾個壓縮檔：(檔名, 位元組數, sha256)
ENGINE_FILES = {
    "cpu": [(f"llama-{ENGINE_TAG}-bin-win-cpu-x64.zip", 25038105,
             "8b3639c62c6c41b8134797ea9beedd6b6c26caad6a1ed99a3d5a2802262923d7")],
    "cuda": [(f"llama-{ENGINE_TAG}-bin-win-cuda-12.4-x64.zip", 357975625,
              "2c0a950896f370185f704ef0bc4085278462c444049c47158007af870515aef5"),
             ("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
              "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6")],
}
ENGINE_MB = {kind: round(sum(size for _, size, _ in files) / 1e6) for kind, files in ENGINE_FILES.items()}


class DownloadError(Exception):
    pass


# ---- 語音辨識用顯示卡：除了翻譯引擎帶的 CUDA 檔案（cublas），還需要 NVIDIA 的 cuDNN。
# NVIDIA 把它放在 PyPI 上（nvidia-cudnn-cu12），是一個壓縮檔，裡面的 DLL 拿出來就能用。
CUDNN_INDEX = "https://pypi.org/pypi/nvidia-cudnn-cu12/json"
CUDNN_MB = 700


def cudnn_dir(data: Path) -> Path:
    return data / "engine" / "cudnn"


def has_cudnn(data: Path) -> bool:
    return any(cudnn_dir(data).glob("cudnn*.dll"))


def cuda_runtime_dir(data: Path) -> Path | None:
    """翻譯引擎（顯示卡版）帶來的 CUDA 檔案放在哪個資料夾；沒有顯示卡版的引擎就沒有。"""
    for p in engine_dir(data, "cuda").rglob("cublas64_*.dll"):
        return p.parent
    return None


def pick_cudnn_wheel(index: dict) -> tuple[str, int, str] | None:
    """從 PyPI 的清單裡挑最新的 cuDNN 9（Windows 64 位元）。回傳（網址, 位元組數, PyPI 登記的 sha256）。"""
    best = None
    for version, files in (index.get("releases") or {}).items():
        try:
            key = tuple(int(n) for n in version.split("."))
        except ValueError:
            continue
        if not key or key[0] != 9:
            continue
        for f in files:
            if str(f.get("filename", "")).endswith("win_amd64.whl") and not f.get("yanked"):
                if best is None or key > best[0]:
                    best = (key, f["url"], int(f.get("size") or 0), str((f.get("digests") or {}).get("sha256") or ""))
    return best[1:] if best else None


def get_cudnn(data: Path, progress=None, cancel=None) -> Path:
    """下載 cuDNN，把裡面的 DLL 放進資料夾。progress(已下載位元組, 總位元組)。"""
    folder = cudnn_dir(data)
    if has_cudnn(data):
        return folder
    try:
        req = urllib.request.Request(CUDNN_INDEX, headers={**UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            picked = pick_cudnn_wheel(json.loads(r.read().decode("utf-8")))
    except Exception as e:  # noqa: BLE001
        raise DownloadError(f"cuDNN list: {e}") from e
    if not picked:
        raise DownloadError("no Windows build of cuDNN 9 was found")
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / "cudnn.whl"
    # cuDNN 是 NVIDIA 自己放在 PyPI 上的，跟著最新的 9.x 走；這裡核對的是 PyPI 登記的雜湊（擋下載壞掉、被換過的檔案）
    fetch(picked[0], wheel, progress, cancel, check=(picked[1], picked[2]) if picked[2] else None)
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if name.lower().endswith(".dll") and "/bin/" in "/" + info.filename.replace("\\", "/"):
                (folder / name).write_bytes(z.read(info))
    wheel.unlink()
    if not has_cudnn(data):
        raise DownloadError("the cuDNN download did not contain the expected files")
    return folder


def engine_dir(data: Path, kind: str) -> Path:
    return data / "engine" / kind


def find_server(folder: Path) -> Path | None:
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    if folder.is_dir():
        for p in folder.rglob(name):
            return p
    return None


def model_path(data: Path, model: str) -> Path:
    return data / "models" / MODELS[model][1]


def missing(data: Path, mode: dict, need_engine: bool = True) -> list[dict]:
    """還缺哪些檔案。每一項：{"key", "mb"}，key 是介面上顯示用的名稱代碼。"""
    out = []
    if need_engine and not os.environ.get("LLAMA_SERVER") and not find_server(engine_dir(data, mode["engine"])):
        out.append({"key": "engine", "kind": mode["engine"], "mb": ENGINE_MB[mode["engine"]]})
    if not model_path(data, mode["model"]).exists():
        out.append({"key": "model", "model": mode["model"], "mb": MODELS[mode["model"]][2]})
    if any(not (data / "models" / rel).exists() for _, rel, _ in OCR_FILES):
        out.append({"key": "ocr", "mb": sum(mb for _, _, mb in OCR_FILES)})
    if any(not (data / "models" / rel).exists() for _, rel, _ in WHISPER_FILES):
        out.append({"key": "whisper", "mb": sum(mb for _, _, mb in WHISPER_FILES)})
    return out


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def matches(path: Path, check: tuple[int, str]) -> bool:
    """這個檔案是不是我們要的那一個：大小和 sha256 都要對。"""
    size, digest = check
    return (not size or path.stat().st_size == size) and sha256_of(path) == digest.lower()


def fetch(url: str, dest: Path, progress=None, cancel=None, attempts: int = 8, check: tuple[int, str] | None = None) -> None:
    """
    下載一個檔案，支援續傳。完成後才會改成正式檔名。progress(已下載位元組, 總位元組)。
    check：（位元組數, sha256）。有給的話下載完要核對，不對就整個重抓一次（續傳接到別的版本的檔案時會這樣）；
    還是不對就丟出 DownloadError，檔案不會留下來、也不會被拿來用。
    """
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for again in (True, False):
        _download(url, dest, part, progress, cancel, attempts)
        if check is None or matches(part, check):
            break
        part.unlink(missing_ok=True)
        if not again:
            raise DownloadError(f"{dest.name}: the downloaded file is not the expected one (size or sha256 differs)")
    os.replace(part, dest)


def _download(url: str, dest: Path, part: Path, progress, cancel, attempts: int) -> None:
    last_error = None
    for attempt in range(attempts):
        if cancel and cancel():
            raise DownloadError("cancelled")
        try:
            done = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(url, headers=dict(UA))
            if done:
                req.add_header("Range", f"bytes={done}-")
            try:
                resp = urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as e:
                if e.code == 416 and done:  # 暫存檔其實已經完整
                    break
                raise
            with resp:
                if done and resp.status != 206:
                    done = 0
                total = done + int(resp.headers.get("Content-Length", 0))
                with open(part, "ab" if done else "wb") as f:
                    while chunk := resp.read(1 << 18):
                        if cancel and cancel():
                            raise DownloadError("cancelled")
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            if total and done < total:
                raise IOError("connection lost")
            break
        except DownloadError:
            raise
        except Exception as e:  # noqa: BLE001
            last_error = e
            time.sleep(min(20, 3 * (attempt + 1)))
    else:
        raise DownloadError(f"{dest.name}: {last_error}")


def pinned() -> list[tuple[str, int, str]]:
    """所有鎖定的下載：[(網址, 位元組數, sha256)]。自我檢查用它確認每一個都還抓得到。"""
    out = [(url, *CHECKS[name]) for url, name, _ in MODELS.values()]
    out += [(url, *CHECKS[rel]) for url, rel, _ in OCR_FILES + WHISPER_FILES]
    out += [(f"{ENGINE_BASE}/{name}", size, digest) for files in ENGINE_FILES.values() for name, size, digest in files]
    return out


def get_engine(data: Path, kind: str, progress=None, cancel=None) -> Path:
    folder = engine_dir(data, kind)
    exe = find_server(folder)
    if exe:
        return exe
    if sys.platform != "win32":
        raise DownloadError("engine download is only available on Windows; set LLAMA_SERVER instead")
    assets = ENGINE_FILES[kind]
    folder.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, tuple[int, int]] = {}
    for name, size, digest in assets:
        zpath = folder / name

        def step(done, total, name=name):
            sizes[name] = (done, total)
            if progress:
                progress(sum(d for d, _ in sizes.values()), sum(t for _, t in sizes.values()))

        fetch(f"{ENGINE_BASE}/{name}", zpath, step, cancel, check=(size, digest))
    for name, _, _ in assets:
        zpath = folder / name
        with zipfile.ZipFile(zpath) as z:
            z.extractall(folder)
        zpath.unlink()
    exe = find_server(folder)
    if not exe:
        raise DownloadError("llama-server.exe was not in the downloaded engine")
    return exe


def download_all(data: Path, items: list[dict], on_progress=None, cancel=None) -> None:
    """
    依序下載 items（missing() 的結果）。
    on_progress({"key": 目前這一項, "done_mb", "total_mb", "index", "count"})
    """
    total_mb = sum(i["mb"] for i in items) or 1
    finished_mb = 0.0
    last = [0.0]

    def report(key, index, item_mb, frac, force=False):
        now = time.time()
        if not force and now - last[0] < 0.5:
            return
        last[0] = now
        if on_progress:
            on_progress({"key": key, "index": index, "count": len(items),
                         "done_mb": round(finished_mb + item_mb * frac), "total_mb": total_mb})

    for index, item in enumerate(items):
        key, mb = item["key"], item["mb"]
        report(key, index, mb, 0, force=True)
        if key == "engine":
            get_engine(data, item["kind"], lambda d, t: report(key, index, mb, d / t if t else 0), cancel)
        else:
            if key == "model":
                url, name, size = MODELS[item["model"]]
                files = [(url, name, size)]
            else:
                files = OCR_FILES if key == "ocr" else WHISPER_FILES
            file_total = sum(f[2] for f in files) or 1
            before = 0
            for url, rel, fmb in files:
                fetch(url, data / "models" / rel,
                      lambda d, t, before=before, fmb=fmb: report(
                          key, index, mb, (before + fmb * (d / t if t else 0)) / file_total), cancel, check=CHECKS[rel])
                before += fmb
        finished_mb += mb
        report(key, index, 0, 0, force=True)
