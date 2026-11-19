"""
第一次使用時下載需要的檔案（存在使用者的資料夾，之後離線也能用）：
  翻譯引擎 llama.cpp、翻譯模型 TranslateGemma、螢幕文字辨識模型、語音辨識模型 Whisper。
下載到一半中斷的話，下次會從中斷的地方接著下載。
"""

import json
import re
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UA = {"User-Agent": "LiveLingo"}
HF = "https://huggingface.co"
RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"

MODELS = {
    "4b": (f"{HF}/bullerwins/translategemma-4b-it-GGUF/resolve/main/translategemma-4b-it-Q4_K_M.gguf",
           "translategemma-4b.gguf", 2490),
    "12b": (f"{HF}/bullerwins/translategemma-12b-it-GGUF/resolve/main/translategemma-12b-it-Q4_K_M.gguf",
            "translategemma-12b.gguf", 7300),
}
OCR_FILES = [
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/detection/v3/det.onnx", "ocr/det.onnx", 3),
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/languages/korean/rec.onnx", "ocr/korean_rec.onnx", 13),
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/languages/korean/dict.txt", "ocr/korean_dict.txt", 1),
]
_WHISPER = f"{HF}/deepdml/faster-whisper-large-v3-turbo-ct2/resolve/main"
WHISPER_FILES = [(f"{_WHISPER}/{n}", f"whisper/{n}", mb) for n, mb in (
    ("config.json", 1), ("preprocessor_config.json", 1), ("tokenizer.json", 3), ("vocabulary.json", 1),
    ("model.bin", 1620))]
ENGINE_MB = {"cpu": 30, "cuda": 600}
# 先找指定的版本；檔名規則如果改了，退而求其次
ENGINE_PATTERNS = {
    "cpu": [[r"^llama-.*-bin-win-cpu-x64\.zip$"], [r"^llama-.*-bin-win-(avx2|avx|noavx)-x64\.zip$"],
            [r"^llama-.*-bin-win-vulkan-x64\.zip$"]],
    "cuda": [[r"^llama-.*-bin-win-cuda-12[.\d]*-x64\.zip$", r"^cudart-llama-bin-win-cuda-12[.\d]*-x64\.zip$"],
             [r"^llama-.*-bin-win-cuda-[.\d]*-x64\.zip$", r"^cudart-llama-bin-win-cuda-[.\d]*-x64\.zip$"]],
}


class DownloadError(Exception):
    pass


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


def fetch(url: str, dest: Path, progress=None, cancel=None, attempts: int = 8) -> None:
    """下載一個檔案，支援續傳。完成後才會改成正式檔名。progress(已下載位元組, 總位元組)。"""
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
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
    os.replace(part, dest)


def pick_engine_assets(releases: list, kind: str) -> list[tuple[str, str]] | None:
    """從 GitHub 的版本清單裡，找最新一個有需要的 Windows 檔案的版本，回傳 [(檔名, 下載網址)]。"""
    for patterns in ENGINE_PATTERNS[kind]:
        for rel in releases:
            found = []
            for pat in patterns:
                rx = re.compile(pat)
                hit = next((a for a in rel.get("assets", []) if rx.search(a.get("name", ""))), None)
                if hit:
                    found.append((hit["name"], hit["browser_download_url"]))
            if len(found) == len(patterns):
                return found
    return None


def list_engine_releases() -> list:
    req = urllib.request.Request(RELEASES_API, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def get_engine(data: Path, kind: str, progress=None, cancel=None) -> Path:
    folder = engine_dir(data, kind)
    exe = find_server(folder)
    if exe:
        return exe
    if sys.platform != "win32":
        raise DownloadError("engine download is only available on Windows; set LLAMA_SERVER instead")
    try:
        assets = pick_engine_assets(list_engine_releases(), kind)
    except Exception as e:  # noqa: BLE001
        raise DownloadError(f"engine list: {e}") from e
    if not assets:
        raise DownloadError("no Windows build of the engine was found")
    folder.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, tuple[int, int]] = {}
    for name, url in assets:
        zpath = folder / name

        def step(done, total, name=name):
            sizes[name] = (done, total)
            if progress:
                progress(sum(d for d, _ in sizes.values()), sum(t for _, t in sizes.values()))

        fetch(url, zpath, step, cancel)
    for name, _ in assets:
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
                          key, index, mb, (before + fmb * (d / t if t else 0)) / file_total), cancel)
                before += fmb
        finished_mb += mb
        report(key, index, 0, 0, force=True)
