"""啟動、關閉本機的翻譯引擎（llama.cpp 的 llama-server）。"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
        else:
            os.kill(pid, 15)
    except Exception:  # noqa: BLE001
        pass


class Engine:
    def __init__(self, exe: Path | None, model: Path, log_path: Path, threads: int = 0, gpu_layers: int = 0):
        if os.environ.get("LLAMA_SERVER"):
            exe = Path(os.environ["LLAMA_SERVER"])
        self.exe = exe
        self.model = model
        self.log_path = log_path
        self.threads = threads
        self.gpu_layers = gpu_layers
        self.proc: subprocess.Popen | None = None
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._pid_file = log_path.with_name("engine.pid")

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _kill_leftover(self) -> None:
        """上次如果沒有正常關閉，引擎可能還留在背景吃記憶體，先把它關掉。"""
        try:
            pid, name = self._pid_file.read_text(encoding="utf-8").split("\n")[:2]
            pid = int(pid)
        except Exception:  # noqa: BLE001
            return
        try:
            if sys.platform == "win32":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                     text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
                                     stdin=subprocess.DEVNULL).stdout
                alive = "llama-server" in out.lower()
            else:
                alive = "llama-server" in Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
        except Exception:  # noqa: BLE001
            alive = False
        if alive:
            _kill(pid)
        self._pid_file.unlink(missing_ok=True)

    def command(self) -> list[str]:
        # 這組參數是實測過的：--chat-template gemma 少了引擎會開不起來。
        # CPU：--swa-full 加上多個 slot 讓每種翻譯方向各留一份快取，翻譯約快一倍。
        # 顯示卡：本來就夠快，改用比較小的快取，把顯示記憶體留給遊戲。
        args = [str(self.exe), "-m", str(self.model), "--chat-template", "gemma",
                "--host", "127.0.0.1", "--port", str(self.port)]
        if self.gpu_layers > 0:
            args += ["-c", "4096", "-np", "2", "-ngl", str(self.gpu_layers)]
        else:
            args += ["-c", "8192", "-np", "4", "--swa-full", "--cache-ram", "0"]
        if self.threads > 0:
            args += ["-t", str(self.threads)]
        return args

    def start(self) -> None:
        if not self.exe or not Path(self.exe).exists():
            raise RuntimeError("the translation engine is missing")
        if not self.model.exists():
            raise RuntimeError("the translation model is missing")
        self._kill_leftover()
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     cwd=str(Path(self.exe).parent), creationflags=flags)
        try:
            self._pid_file.write_text(f"{self.proc.pid}\nllama-server", encoding="utf-8")
        except OSError:
            pass

    def wait_ready(self, timeout: float = 240) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError("the translation engine stopped while starting (see engine.log)")
            if self.healthy():
                return True
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self._pid_file.unlink(missing_ok=True)
