"""
作品：你現在在看哪一部影片、哪一個網頁。LiveLingo 不連網、不讀網址，只看讀取來源那個視窗的標題，
例如「某某影片 - YouTube - Google Chrome」整理成「某某影片」，整理後的標題就是這一部的身分（key 是它的指紋）。

翻過的句子（memory.py）會記下每一句是在哪一部看到的，所以：
- 同一部再看一次，或朋友傳來這一部的分享檔，一開始就知道「這部翻過了」；
- 語音辨識每次聽到的字可能差一點點，只在同一部裡面找「幾乎一樣」的句子，才不會拿錯別部的。

標題只存在這台電腦；只有使用者自己選「匯出這一部」時，才會把標題寫進分享檔。
之後連網時，伺服器也用同一個 key 認作品（只送指紋，不送標題）。
"""

import hashlib
import re

MAX_TITLE = 120

# 視窗標題結尾常見的程式名、網站名：一層一層拿掉（瀏覽器在最外面，網站在裡面）
_TAILS = [
    r"google chrome", r"chromium", r"mozilla firefox", r"firefox", r"microsoft​? edge", r"edge", r"brave", r"opera",
    r"vivaldi", r"arc", r"whale", r"naver whale",
    r"youtube", r"twitch", r"netflix", r"bilibili", r"哔哩哔哩", r"嗶哩嗶哩", r"niconico", r"ニコニコ動画", r"chzzk", r"치지직",
    r"afreecatv", r"soop", r"kick", r"disney\+", r"prime video", r"crunchyroll", r"viu", r"wetv", r"iqiyi", r"愛奇藝", r"爱奇艺",
    r"vlc media player", r"potplayer", r"mpc-hc", r"mpc-be", r"media player", r"movies & tv", r"電影與電視",
]
_TAIL = re.compile(r"\s*(?:[-–—|·_]+\s*)(?:" + "|".join(_TAILS) + r")\s*$", re.I)
_PROFILE = re.compile(r"\s*[-–—]\s*(?:personal|個人|个人|work|工作|profile \d+|設定檔 \d+)\s*$", re.I)
_COUNT = re.compile(r"^\s*\(\d+\+?\)\s*")         # 「(3) 影片名稱」開頭的通知數字
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

# 整理完只剩這些的，不是一部作品（首頁、新分頁、播放器本身）
GENERIC = {
    "", "youtube", "twitch", "netflix", "bilibili", "new tab", "新分頁", "新标签页", "새 탭", "新しいタブ", "home", "首頁", "首页",
    "google chrome", "mozilla firefox", "microsoft edge", "vlc media player", "potplayer", "media player", "untitled", "無標題",
    "subscriptions", "訂閱內容", "history", "記錄", "watch later", "稍後觀看", "discord", "settings", "設定",
}


def clean_title(title: str) -> str:
    """給人看的作品名稱：拿掉瀏覽器、網站、播放器的名字和開頭的通知數字。"""
    t = _INVISIBLE.sub("", str(title or ""))
    t = re.sub(r"\s+", " ", t).strip()
    t = _COUNT.sub("", t)
    for _ in range(4):
        before = t
        t = _PROFILE.sub("", t)
        t = _TAIL.sub("", t).strip()
        if t == before:
            break
    return t[:MAX_TITLE].strip()


def work_key(title: str) -> str:
    """這一部的身分（指紋）；標題整理完不像一部作品（太短、只剩網站名）就是空字串。"""
    t = clean_title(title)
    low = t.lower()
    if len(low) < 3 or low in GENERIC:
        return ""
    return hashlib.sha1(low.encode("utf-8")).hexdigest()[:12]


def pick_window(windows: list[dict], exe: str = "", point: tuple[int, int] | None = None, skip=("livelingo",)) -> dict | None:
    """從看得到的視窗裡（apps.windows()，最上面的在前）挑出讀取來源的那一個。
    exe：讀取的區域跟著某個程式，或只聽某個程式的聲音 → 那個程式最上面的視窗。
    point：區域的中心點 → 蓋在那一點最上面的視窗。都沒有 → 最前面的視窗。LiveLingo 自己的視窗不算。"""
    def mine(w):
        name = str(w.get("exe") or "").lower()
        return any(name.startswith(s) for s in skip)

    wins = [w for w in windows if not mine(w) and not w.get("minimized") and str(w.get("title") or "").strip()]
    if exe:
        exe = exe.lower()
        return next((w for w in wins if str(w.get("exe") or "").lower() == exe), None)
    if point is not None:
        x, y = point
        for w in wins:
            r = w.get("rect") or ()
            if len(r) == 4 and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
                return w
        return None
    return wins[0] if wins else None
