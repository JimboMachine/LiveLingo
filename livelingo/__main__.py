"""LiveLingo 的進入點。"""

import os
import sys
import traceback
from pathlib import Path

# 語音辨識和其他元件各自帶了一份 OpenMP，Windows 上同時載入時要加這個才不會被擋下來
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _arg(name: str, default: str | None = None) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> int:
    if "--select-region" in sys.argv:
        from .region import select

        select(Path(_arg("--select-region")), _arg("--lang", "zh-TW"), _arg("--mode", "chat"))
        return 0
    if "--quick-input" in sys.argv:      # 快速輸入框：打的字寫到檔案就結束
        from .quickbox import ask

        ask(Path(_arg("--quick-input")), _arg("--lang", "zh-TW"))
        return 0
    if "--reply-box" in sys.argv:      # 回話建議的小框：選的那句寫到檔案就結束
        from .quickbox import choose

        choose(Path(_arg("--reply-box")), Path(_arg("--reply-out")), _arg("--lang", "zh-TW"), _arg("--their", "ko"))
        return 0
    if "--card" in sys.argv:      # 翻譯結果小卡（翻選取的字、框選翻譯）：只顯示，不寫東西
        from .quickbox import card

        card(Path(_arg("--card")), _arg("--lang", "zh-TW"), _arg("--their", "ko"))
        return 0
    if "--phrase-box" in sys.argv:      # 常用句庫的小框：選的那句譯文寫到檔案就結束
        from .quickbox import phrases

        phrases(Path(_arg("--phrase-box")), Path(_arg("--phrase-out")), _arg("--lang", "zh-TW"), _arg("--their", "ko"))
        return 0
    if "--remove-store" in sys.argv:      # 卸載程式叫的：大檔案搬到別的磁碟的話，那邊的也刪掉（只刪有記號檔的資料夾）
        import json

        from . import storage
        from .paths import data_dir

        data = data_dir()
        try:
            saved = json.loads((data / "config.json").read_text(encoding="utf-8")).get("store_dir") or ""
        except (OSError, ValueError):
            saved = ""
        if storage.remove_moved(data, str(saved)):
            try:      # 使用者選擇留下設定的話，下次安裝不要還指著已經刪掉的資料夾
                kept = json.loads((data / "config.json").read_text(encoding="utf-8"))
                kept.pop("store_dir", None)
                (data / "config.json").write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                pass
        return 0
    if "--selftest" in sys.argv:
        from .selftest import run as selftest

        return selftest(Path(_arg("--selftest")), window="--no-window" not in sys.argv)
    if "--probe-app-audio" in sys.argv:      # 健康檢查用：試收自己這個程序的聲音，結果寫到檔案
        from .core.procaudio import probe

        return probe(_arg("--probe-app-audio"))
    if "--data-dir" in sys.argv:      # 資料放在哪個資料夾（發行流程用它確認免安裝版的資料真的放在程式旁邊）
        from .paths import data_dir

        out = _arg("--data-dir")
        if out:
            Path(out).write_text(str(data_dir()), encoding="utf-8")
        else:
            print(data_dir())
        return 0
    if "--version" in sys.argv:
        from . import __version__

        out = _arg("--version")
        if out:
            Path(out).write_text(__version__, encoding="utf-8")
        else:
            print(__version__)
        return 0

    from . import oswin, window
    from .paths import data_dir
    from .server import Server
    from .service import Service

    data = data_dir()
    if not oswin.acquire_single_instance():
        # 已經開著一個了：請它把視窗叫到前面，這個就結束
        try:
            import json
            import urllib.request

            run = json.loads((data / "run.json").read_text(encoding="utf-8"))
            req = urllib.request.Request(f"http://127.0.0.1:{run['port']}/api/focus?t={run['token']}",
                                         data=b"{}", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
        except Exception:  # noqa: BLE001
            pass
        return 0

    service = Service(data)
    from . import safemode

    if safemode.requested():      # 按住 Shift 開（或加 --safe）：這次用預設設定（2.21.0）
        service.safe_start()
    service.watch_errors()      # 沒被接住的錯誤寫進紀錄檔，並在畫面上說一聲

    def exit_now():
        # 收尾出錯也一定要結束，而且翻譯引擎一定要關：留在背景的話會佔住程式檔，更新換不掉
        try:
            service.shutdown()
        except Exception:  # noqa: BLE001
            service.log(traceback.format_exc())
        try:
            if service.engine:
                service.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        (data / "run.json").unlink(missing_ok=True)
        # 「我還開著」的記號不在這裡放掉：留給 Windows 在程式真的結束時收。安裝程式看到記號消失就開始換檔，
        # 先放掉的話程式其實還要幾秒才結束，檔案還被佔著（2.4.2 那次更新就這樣把程式裝壞了）
        os._exit(0)

    service.exit_now = exit_now
    try:
        server = Server(service)
        server.start()
        try:
            import json

            (data / "run.json").write_text(json.dumps({"port": server.port, "token": server.token}), encoding="utf-8")
        except OSError:
            pass
        if os.environ.get("LIVELINGO_PRINT_URL"):
            print(server.url, flush=True)
        window.run(server.url, service, start=service.start)
    except Exception:  # noqa: BLE001
        service.log(traceback.format_exc())
        raise
    finally:
        service.shutdown()
        (data / "run.json").unlink(missing_ok=True)
        oswin.release_single_instance()
    return 0


if __name__ == "__main__":
    code = main()
    # 背景還有收音之類的執行緒，視窗關掉就直接結束整個程式
    os._exit(code)
