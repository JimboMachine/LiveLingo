"""
問題報告：把這台電腦上 LiveLingo 的狀況整理成一份文字，給幫忙看問題的人。

使用者先在畫面上看到整份內容，再自己決定要不要存成檔案、要不要傳給別人。這個檔案不會連到任何地方，也不會自己送出任何東西。

不放進去的：對話內容、對話歷史、詞彙表、教過的句子、聲音、畫面截圖、聊天區和版面取的名字。
Windows 帳號名稱、電腦名稱和個人資料夾的路徑會換成 <user>、<pc>、%USERPROFILE%。
紀錄檔裡如果有哪一行剛好含著對話裡的整句話，那一行不放（正常不會有，這是多一層保險）。
"""

import os
import platform
import re
import time
from pathlib import Path

LOG_LINES = 200                    # app.log 最後幾行
ENGINE_LINES = 60                  # engine.log 最後幾行
LINE_MAX = 600                     # 一行最長幾個字，太長的截掉
NOTE_MAX = 2000                    # 使用者自己寫的說明最長幾個字
SAID_MIN = 6                       # 幾個字以上的整句話才拿來比對紀錄檔
PLAIN_NAMES = {"user", "users", "admin", "administrator", "owner", "guest", "public", "default", "livelingo", "root", "home"}

TEXT = {
    "zh-TW": {
        "title": "LiveLingo 問題報告",
        "intro": ["這份報告只在你的電腦上，還沒有傳給任何人。要不要傳、傳給誰，由你決定。",
                  "裡面沒有對話內容、對話歷史、詞彙表、教過的句子，也沒有聲音和畫面。",
                  "裡面有：版本、電腦規格、各項檢查的結果、設定、裝置和程式的名稱、最近的紀錄。帳號名稱和個人資料夾已經換掉。"],
        "note": "發生了什麼事", "app": "LiveLingo", "computer": "這台電腦", "health": "健康檢查", "sources": "收音和螢幕",
        "storage": "儲存空間", "usage": "使用數字（只算次數，沒有內容）", "settings": "設定", "log": "最近的紀錄", "engine_log": "翻譯引擎的紀錄",
        "dropped": "有 {n} 行因為含著對話裡的句子，沒有放進來。", "none": "（沒有）",
    },
    "zh-CN": {
        "title": "LiveLingo 问题报告",
        "intro": ["这份报告只在你的电脑上，还没有发给任何人。要不要发、发给谁，由你决定。",
                  "里面没有对话内容、对话历史、词汇表、教过的句子，也没有声音和画面。",
                  "里面有：版本、电脑配置、各项检查的结果、设置、设备和程序的名称、最近的日志。账号名称和个人文件夹已经替换掉。"],
        "note": "发生了什么事", "app": "LiveLingo", "computer": "这台电脑", "health": "健康检查", "sources": "收音和屏幕",
        "storage": "存储空间", "usage": "使用数字（只算次数，没有内容）", "settings": "设置", "log": "最近的日志", "engine_log": "翻译引擎的日志",
        "dropped": "有 {n} 行因为含有对话里的句子，没有放进来。", "none": "（没有）",
    },
    "en": {
        "title": "LiveLingo problem report",
        "intro": ["This report is only on your computer. It has not been sent to anyone. Whether to send it, and to whom, is up to you.",
                  "It does not contain your conversations, history, word list, taught sentences, audio or screen images.",
                  "It contains: the version, your computer's specs, the check results, settings, device and program names, and recent log lines. Your account name and personal folder have been replaced."],
        "note": "What happened", "app": "LiveLingo", "computer": "This computer", "health": "Health check", "sources": "Audio and screen",
        "storage": "Storage", "usage": "Usage numbers (counts only, no content)", "settings": "Settings", "log": "Recent log", "engine_log": "Translation engine log",
        "dropped": "{n} lines were left out because they contained a sentence from a conversation.", "none": "(none)",
    },
    "ko": {
        "title": "LiveLingo 문제 보고서",
        "intro": ["이 보고서는 내 컴퓨터에만 있고, 아직 누구에게도 보내지 않았습니다. 보낼지, 누구에게 보낼지는 직접 정합니다.",
                  "대화 내용, 대화 기록, 단어장, 가르친 문장, 소리, 화면은 들어 있지 않습니다.",
                  "들어 있는 것: 버전, 컴퓨터 사양, 점검 결과, 설정, 장치와 프로그램 이름, 최근 로그. 계정 이름과 개인 폴더는 다른 글자로 바꿨습니다."],
        "note": "무슨 일이 있었나요", "app": "LiveLingo", "computer": "이 컴퓨터", "health": "상태 점검", "sources": "소리와 화면",
        "storage": "저장 공간", "usage": "사용 숫자 (횟수만, 내용 없음)", "settings": "설정", "log": "최근 로그", "engine_log": "번역 엔진 로그",
        "dropped": "대화에 나온 문장이 들어 있어서 {n}줄을 뺐습니다.", "none": "(없음)",
    },
    "ja": {
        "title": "LiveLingo 問題レポート",
        "intro": ["このレポートはこのパソコンの中にだけあり、まだ誰にも送られていません。送るかどうか、誰に送るかは自分で決められます。",
                  "会話の内容、会話の履歴、単語リスト、教えた文、音声、画面は入っていません。",
                  "入っているもの：バージョン、パソコンの性能、各チェックの結果、設定、デバイスとアプリの名前、最近のログ。アカウント名と個人フォルダーは置き換えてあります。"],
        "note": "何が起きたか", "app": "LiveLingo", "computer": "このパソコン", "health": "ヘルスチェック", "sources": "音声と画面",
        "storage": "ストレージ", "usage": "利用状況の数字（回数だけ、内容なし）", "settings": "設定", "log": "最近のログ", "engine_log": "翻訳エンジンのログ",
        "dropped": "会話に出てきた文が含まれていたため、{n} 行を除きました。", "none": "（なし）",
    },
}


def computer() -> dict:
    """電腦規格：系統、處理器、幾個核心、記憶體。查不到的就不列。"""
    out = {"system": platform.platform(), "machine": platform.machine(), "cpu": platform.processor(), "cores": os.cpu_count() or 0}
    try:
        if os.name == "nt":
            import ctypes

            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            mem = Memory()
            mem.length = ctypes.sizeof(Memory)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                out["ram_gb"], out["ram_free_gb"] = round(mem.total / 2**30, 1), round(mem.free / 2**30, 1)
        else:
            out["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    return {k: v for k, v in out.items() if v}


def who() -> dict:
    """這台電腦上要換掉的字：個人資料夾、帳號名稱、電腦名稱。"""
    try:
        home = str(Path.home())
    except Exception:  # noqa: BLE001
        home = ""
    return {"home": home, "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "pc": os.environ.get("COMPUTERNAME") or ""}


def _name_pattern(name: str):
    name = str(name or "").strip()
    if len(name) < 3 or name.lower() in PLAIN_NAMES:      # 太短或太普通的名字整個換掉會把報告弄亂；路徑那邊還是會換
        return None
    return re.compile(r"(?<![0-9A-Za-z_])" + re.escape(name) + r"(?![0-9A-Za-z_])", re.IGNORECASE)


def scrub(text: str, home: str = "", user: str = "", pc: str = "") -> str:
    """把個人資料夾的路徑換成 %USERPROFILE%，帳號名稱換成 <user>，電腦名稱換成 <pc>。"""
    home = str(home or "").rstrip("\\/")
    if len(home) > 3:      # 不換掉 C:\ 或 / 這種
        forms = {home, home.replace("\\", "/"), home.replace("\\", "\\\\"), home.replace("/", "\\")}
        for form in sorted(forms, key=len, reverse=True):
            text = re.sub(re.escape(form), lambda _m: "%USERPROFILE%", text, flags=re.IGNORECASE)
    for name, mark in ((user, "<user>"), (pc, "<pc>")):
        pattern = _name_pattern(name)
        if pattern:
            text = pattern.sub(mark, text)
    return text


def tail(path: Path, lines: int) -> list[str]:
    """檔案最後幾行（讀不到就是空的）。"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 400_000))
            rows = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if size > 400_000:
        rows = rows[1:]      # 第一行可能是切到一半的
    return [row[:LINE_MAX] for row in rows[-lines:] if row.strip()]


def drop_said(rows: list[str], said) -> tuple[list[str], int]:
    """含著對話裡整句話的行不要。said：最近的原文和翻譯。回傳（留下來的, 拿掉幾行）。"""
    words = {s.strip().lower() for s in said if s and len(s.strip()) >= SAID_MIN}
    if not words:
        return rows, 0
    kept = [row for row in rows if not any(w in row.lower() for w in words)]
    return kept, len(rows) - len(kept)


def _value(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v is None or v == "":
        return "-"
    if isinstance(v, (list, tuple)):
        return ", ".join(_value(i) for i in v) if v else "-"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_value(i)}" for k, i in v.items()) if v else "-"
    return " ".join(str(v).split())[:LINE_MAX]


def build(lang: str, sections: list[tuple[str, object]], note: str = "", when: float | None = None, dropped: int = 0,
          hide: dict | None = None) -> str:
    """
    排成一份文字。sections：[(段落代碼, 內容)]，內容是 dict（一行一個「名稱: 值」）或 list（一行一個）。
    hide：{"home","user","pc"} 要換掉的字（沒給就用這台電腦的）。回傳的就是使用者會看到、會存下來的全部內容。
    """
    text = TEXT.get(lang) or TEXT["en"]
    lines = [text["title"], time.strftime("%Y-%m-%d %H:%M", time.localtime(when or time.time())), "", *text["intro"]]
    note = "\n".join(line.rstrip() for line in str(note or "").strip()[:NOTE_MAX].splitlines())
    if note:
        lines += ["", f"== {text['note']} ==", *note.split("\n")]
    for code, content in sections:
        lines += ["", f"== {text.get(code, code)} =="]
        if isinstance(content, dict):
            rows = [f"{k}: {_value(v)}" for k, v in content.items()]
        else:
            rows = [_value(row) if not isinstance(row, str) else row[:LINE_MAX] for row in content]
        lines += rows or [text["none"]]
        if code == "log" and dropped:
            lines.append(text["dropped"].format(n=dropped))
    return scrub("\r\n".join(lines) + "\r\n", **(hide if hide is not None else who()))


def save(folder: Path, text: str, now: float | None = None) -> Path:
    """存成一個文字檔（加 BOM，舊版記事本才不會亂碼）。存不進去會丟出 OSError。"""
    folder.mkdir(parents=True, exist_ok=True)
    stem = "LiveLingo-report-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    path, n = folder / f"{stem}.txt", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).txt", n + 1
    path.write_text(text, encoding="utf-8-sig", newline="")
    return path
