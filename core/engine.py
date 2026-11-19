"""啟動、關閉本機的翻譯引擎（llama.cpp 的 llama-server）。"""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


class Engine:
    def __init__(self, root: Path, port: int = 8177, threads: int = 0, gpu_layers: int = 0):
        self.root = root
        self.port = port
        self.threads = threads
        self.gpu_layers = gpu_layers
        self.proc: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{port}"

    def _exe(self) -> Path | None:
        if os.environ.get("LLAMA_SERVER"):
            return Path(os.environ["LLAMA_SERVER"])
        name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
        for p in (self.root / "engine").rglob(name):
            return p
        return None

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> None:
        if self.healthy():  # 已經有一個在跑了（例如上次沒關乾淨），直接用
            return
        exe = self._exe()
        model = self.root / "models" / "translategemma-4b.gguf"
        if not exe or not exe.exists():
            raise RuntimeError("找不到翻譯引擎，請先執行 setup.bat")
        if not model.exists():
            raise RuntimeError("找不到翻譯模型，請先執行 setup.bat")
        # 這組參數是實測過的：--chat-template gemma 少了引擎會開不起來；
        # --swa-full 加上多個 slot 讓每種翻譯方向各留一份快取，翻譯約快一倍
        args = [str(exe), "-m", str(model), "--chat-template", "gemma",
                "-c", "8192", "-np", "4", "--swa-full", "--cache-ram", "0",
                "--host", "127.0.0.1", "--port", str(self.port)]
        if self.threads > 0:
            args += ["-t", str(self.threads)]
        if self.gpu_layers > 0:
            args += ["-ngl", str(self.gpu_layers)]
        log = open(self.root / "engine.log", "w", encoding="utf-8", errors="replace")
        flags = 0
        if sys.platform == "win32":
            import ctypes

            # 有黑色命令視窗時讓引擎跟著同一個視窗（視窗被關掉引擎也會一起結束，不會留在背景吃記憶體）；
            # 沒有命令視窗時（pythonw）才另外隱藏起來
            if not ctypes.windll.kernel32.GetConsoleWindow():
                flags = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     cwd=str(exe.parent), creationflags=flags)

    def wait_ready(self, timeout: float = 180) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.healthy():
                return True
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError("翻譯引擎啟動失敗，詳細原因在 engine.log")
            time.sleep(1)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
