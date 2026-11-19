"""
第一次安裝時下載需要的檔案（全部存在這個資料夾裡，之後離線也能用）：
  1. 翻譯引擎 llama.cpp（Windows 版，約 20 MB）
  2. 翻譯模型 TranslateGemma（約 2.5 GB）
  3. 螢幕文字辨識模型（約 16 MB）
  4. 語音辨識模型 Whisper large-v3-turbo（約 1.6 GB）
下載到一半中斷的話，再執行一次會從中斷的地方接著下載。
"""

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
ENGINE = ROOT / "engine"
UA = {"User-Agent": "live-translator-setup"}

HF = "https://huggingface.co"
FILES = [
    (f"{HF}/bullerwins/translategemma-4b-it-GGUF/resolve/main/translategemma-4b-it-Q4_K_M.gguf", MODELS / "translategemma-4b.gguf"),
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/detection/v3/det.onnx", MODELS / "ocr" / "det.onnx"),
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/languages/korean/rec.onnx", MODELS / "ocr" / "korean_rec.onnx"),
    (f"{HF}/monkt/paddleocr-onnx/resolve/main/languages/korean/dict.txt", MODELS / "ocr" / "korean_dict.txt"),
]
WHISPER = f"{HF}/deepdml/faster-whisper-large-v3-turbo-ct2/resolve/main"
FILES += [(f"{WHISPER}/{name}", MODELS / "whisper" / name)
          for name in ("config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json", "model.bin")]
RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, dest: Path) -> None:
    """下載一個檔案，支援續傳。完成後才會改成正式檔名。"""
    if dest.exists() and dest.stat().st_size > 0:
        log(f"已經有了：{dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, 11):
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
                log(f"下載 {dest.name}（{total / 1e6:.0f} MB）…")
                last = time.time()
                with open(part, "ab" if done else "wb") as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last >= 5 and total:
                            last = time.time()
                            log(f"  {done * 100 / total:.0f}%（{done / 1e6:.0f} / {total / 1e6:.0f} MB）")
            if total and done < total:
                raise IOError("連線中斷")
            break
        except Exception as e:  # noqa: BLE001
            log(f"  下載失敗（第 {attempt} 次）：{e}，10 秒後重試…")
            time.sleep(10)
    else:
        sys.exit(f"{dest.name} 一直下載失敗，請檢查網路後再執行一次 setup.bat")
    os.replace(part, dest)


def pick_engine_asset(releases: list, pattern: str) -> tuple[str, str] | None:
    """從 GitHub 的版本清單裡，找最新一個有 Windows 版的，回傳 (檔名, 下載網址)。"""
    rx = re.compile(pattern)
    for rel in releases:
        for a in rel.get("assets", []):
            if rx.search(a.get("name", "")):
                return a["name"], a["browser_download_url"]
    return None


def find_exe(folder: Path) -> Path | None:
    for p in folder.rglob("llama-server.exe"):
        return p
    return None


def get_engine(kind: str = "cpu") -> None:
    if find_exe(ENGINE):
        log("已經有了：翻譯引擎 llama-server.exe")
        return
    log("查詢翻譯引擎的最新版本…")
    req = urllib.request.Request(RELEASES_API, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        releases = json.loads(r.read().decode("utf-8"))
    # 先找指定的版本（預設是純 CPU 版）；檔名規則如果改了，退而求其次找其他不需要額外驅動的 x64 版本
    found = None
    for pattern in (rf"^llama-.*-bin-win-{kind}-x64\.zip$", r"^llama-.*-bin-win-(cpu|avx2|avx|noavx)-x64\.zip$",
                    r"^llama-.*-bin-win-vulkan-x64\.zip$"):
        found = pick_engine_asset(releases, pattern)
        if found:
            break
    if not found:
        sys.exit("找不到 Windows 版的翻譯引擎，請把這個訊息截圖給 Claude。")
    name, url = found
    zpath = ENGINE / name
    fetch(url, zpath)
    log("解壓縮翻譯引擎…")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(ENGINE)
    zpath.unlink()
    if not find_exe(ENGINE):
        sys.exit("解壓縮後找不到 llama-server.exe，請把這個訊息截圖給 Claude。")


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "cpu"
    if sys.platform == "win32":
        get_engine(kind)
    for url, dest in FILES:
        fetch(url, dest)
    log("全部下載完成。")


if __name__ == "__main__":
    main()
