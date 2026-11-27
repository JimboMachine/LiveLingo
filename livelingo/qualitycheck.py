"""翻譯自我檢查（2.14.0）。

用一組以前真的翻錯過、或很容易翻錯的句子（data/quality_cases.json），拿這台電腦上的翻譯引擎重翻一次、逐條打分，
跟上一次的分數比：哪幾句以前對、這次錯了。每次更新之後按一下，就知道這一版有沒有越改越差。
只在這台電腦上翻，不用網路；結果存在 quality.json（最近 MAX_RUNS 次）。翻的時候不用、也不改「翻過的句子」和教過的句子。
"""

import json
import threading
import time
from pathlib import Path

from .core.translate import _leftover, meta_reply, numbers_lost

CASES = Path(__file__).with_name("data") / "quality_cases.json"
MAX_RUNS = 10


def load_cases(path: Path = CASES) -> list[dict]:
    return [c for c in json.loads(path.read_text(encoding="utf-8"))["cases"] if c.get("src") and c.get("target")]


def judge(case: dict, out: str) -> list[str]:
    """這句翻得對不對：回傳沒通過的檢查（空的 = 通過）。每一句都要：有翻出東西、不是在跟人說話、沒有留著原文的字。"""
    failed: list[str] = []
    out = (out or "").strip()
    src, lang, target = case["src"], case.get("lang"), case["target"]
    if not out or out == src.strip():
        return ["empty"]
    if meta_reply(out, src):
        failed.append("meta")
    if lang and _leftover(out, lang, target):
        failed.append("leftover")
    if case.get("has") and not any(w.lower() in out.lower() for w in case["has"]):
        failed.append("has")
    if any(w.lower() in out.lower() for w in case.get("not", [])):
        failed.append("not")
    asks = "?" in out or "？" in out
    if case.get("kind") == "statement" and asks:
        failed.append("became_question")
    if case.get("kind") == "question" and not asks:
        failed.append("not_question")
    if case.get("numbers") and numbers_lost(src, out, target, lang):
        failed.append("numbers")
    return failed


class QualityCheck:
    """跑一次檢查（背景執行緒），一邊跑一邊叫 on_change(state)。translate(case) 回傳譯文。"""

    def __init__(self, folder: Path, on_change=lambda state: None, log=lambda text: None):
        self.path = Path(folder) / "quality.json"
        self.on_change = on_change
        self.log = log
        self.running = False
        self.done = 0
        self.total = 0
        self._stop = threading.Event()

    def runs(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [r for r in data.get("runs", []) if isinstance(r, dict)]
        except Exception:  # noqa: BLE001
            return []

    def state(self) -> dict:
        runs = self.runs()
        last = runs[-1] if runs else None
        prev = runs[-2] if len(runs) > 1 else None
        worse = []
        if last and prev:
            before = {r["id"]: r["ok"] for r in prev.get("results", [])}
            worse = [r["id"] for r in last.get("results", []) if not r["ok"] and before.get(r["id"])]
        return {"running": self.running, "done": self.done, "total": self.total, "last": last,
                "previous": {k: prev[k] for k in ("at", "version", "passed", "total")} if prev else None, "worse": worse}

    def stop(self) -> None:
        self._stop.set()

    def start(self, translate, version: str, model: str, cases: list[dict] | None = None) -> bool:
        if self.running:
            return False
        cases = load_cases() if cases is None else cases
        self.running, self.done, self.total = True, 0, len(cases)
        self._stop.clear()
        threading.Thread(target=self._run, args=(cases, translate, version, model), daemon=True, name="quality").start()
        self.on_change(self.state())
        return True

    def _run(self, cases: list[dict], translate, version: str, model: str) -> None:
        results, started = [], time.time()
        try:
            for case in cases:
                if self._stop.is_set():
                    return
                try:
                    out = translate(case)
                    failed = judge(case, out)
                except Exception as ex:  # noqa: BLE001
                    out, failed = "", ["error"]
                    self.log(f"quality check {case['id']} failed: {ex!r}")
                results.append({"id": case["id"], "src": case["src"], "target": case["target"], "out": out[:300],
                                "ok": not failed, "failed": failed})
                self.done += 1
                self.on_change(self.state())
            run = {"at": time.time(), "version": version, "model": model, "seconds": round(time.time() - started, 1),
                   "passed": sum(1 for r in results if r["ok"]), "total": len(results), "results": results}
            runs = (self.runs() + [run])[-MAX_RUNS:]
            try:
                self.path.write_text(json.dumps({"runs": runs}, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError as ex:
                self.log(f"quality check could not be saved: {ex!r}")
        finally:
            self.running = False
            self.on_change(self.state())
