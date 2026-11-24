"""啟動、關閉本機的翻譯引擎（llama.cpp 的 llama-server）。"""

import contextlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

_dll_lock = threading.Lock()


@contextlib.contextmanager
def own_dlls_only():
    """
    打包好的 LiveLingo 啟動時，PyInstaller 用 SetDllDirectory 把程式資料夾（_internal）加進找 DLL 的地方，
    而這個設定會傳給開出來的程式。引擎因此會載入 _internal 裡的 msvcp140.dll、vcruntime140.dll 並一直佔著，
    LiveLingo 關掉後如果引擎還在，更新就換不掉這些檔案（2.4.2 那次把程式裝壞就是這樣）。
    開引擎的那一刻先把這個設定拿掉，開完再放回去。
    """
    base = getattr(sys, "_MEIPASS", None)
    if sys.platform != "win32" or not base:
        yield
        return
    import ctypes

    k32 = ctypes.windll.kernel32
    with _dll_lock:
        k32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            k32.SetDllDirectoryW(str(base))


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
        # 每次啟動隨機產生的金鑰：引擎只聽這台電腦，但沒有金鑰的話，電腦上任何程式（或瀏覽器裡的網頁）都能叫它翻東西、佔住它。
        # 用環境變數交給引擎，不放在指令列上（指令列別的程式看得到）。
        self.key = secrets.token_urlsafe(24)
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
        # --cache-ram 0：兩種都要關。引擎預設會把翻過的句子的快取另外存在電腦記憶體裡，最多 8 GB，
        # 開久了真的會慢慢長到 8 GB（Jimbo 的電腦用顯示卡跑了一晚，引擎吃掉 9.5 GB 記憶體）。
        # 我們每句都很短，同一個 slot 自己的快取就夠用了，這份額外的快取幾乎用不到。
        args = [str(self.exe), "-m", str(self.model), "--chat-template", "gemma",
                "--host", "127.0.0.1", "--port", str(self.port), "--cache-ram", "0"]
        if self.gpu_layers > 0:
            args += ["-c", "4096", "-np", "2", "-ngl", str(self.gpu_layers)]
        else:
            args += ["-c", "8192", "-np", "4", "--swa-full"]
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
        with own_dlls_only():
            self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         cwd=str(Path(self.exe).parent), creationflags=flags,
                                         env={**os.environ, "LLAMA_API_KEY": self.key})
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
