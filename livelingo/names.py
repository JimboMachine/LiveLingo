"""名字建議和不翻的字（2.14.0）。

不翻的字（keep_words）：使用者列出來的字（gg、lol、ㅋㅋ、朋友的遊戲 ID）翻譯時照原樣留著。
名字建議：對方的話裡一直出現、看起來像遊戲 ID 或名字的字，出現到第 NEED 次就問使用者一次「要不要固定不翻」。
只在這台電腦上比對文字，什麼都不送出去；問過的（不管答什麼）不會再問。
"""

import re
from collections import Counter

MAX_WORDS = 60
MAX_LEN = 30
NEED = 3            # 在幾則不同的話裡出現過才問
MAX_TRACKED = 400   # 最多同時數幾個字（太多就把只出現一次的丟掉）

# 遊戲 ID 的樣子：英文字母開頭，夾著數字、底線，或大小寫混著（xXSniperXx、Faker_99、hide0nbush）
_TOKEN = re.compile(r"(?<![\w@#])[@#]?([A-Za-z][A-Za-z0-9_.\-]{2,23})(?![\w])")
# 很常見、不是名字的英文字（非英文的句子裡也常夾這些）
_COMMON = set("""
ok okay lol lmao lmfao rofl gg wp ggwp gl hf glhf afk brb omg wtf idk tbh imo btw np ty thx pls plz yes no yeah nah
the and you for are but not all any can was one our out get has him his how new now old see two way who did its let
put say she too use her what with this that have from they will your just like when time know take make good some
them than then look only come over also back after work first well even want because these give most game team play
win lose kill dead push gank mid top bot jungle support carry tank heal buff nerf skill item level rank solo duo lane
boss raid quest guild party map stream live chat discord youtube twitch steam riot league valorant minecraft roblox
haha hahaha lolol xd xdd kek pog poggers sus noob nice good bad sorry hello bye
""".split())
_VERSION = re.compile(r"^(?:v?\d|[a-z]{1,3}\d{1,2}$)", re.I)      # mp3、ps5、cs2、v1.2：不是名字


def clean_words(raw) -> list[str]:
    """不翻的字：一串用逗號、頓號、換行分開的字，或一個清單。去掉空的和重複的（不分大小寫）。"""
    if isinstance(raw, str):
        raw = re.split(r"[,，、;；\n]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        word = " ".join(str(item).split())[:MAX_LEN]
        if word and word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out[:MAX_WORDS]


def _latin_only(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and all(ch.isascii() for ch in letters)


def looks_like_id(word: str) -> bool:
    """看起來像遊戲 ID：有數字或底線，或大小寫混在中間（xXSniperXx）。純小寫或開頭大寫的一般英文字不算。"""
    if len(word) < 4 or _VERSION.match(word):
        return False
    inner = word[1:]
    return (any(ch.isdigit() for ch in word) and any(ch.isalpha() for ch in word)) or "_" in word \
        or bool(re.search(r"[a-z][A-Z]", inner))


def candidates(text: str) -> list[str]:
    """這句話裡可能是名字的字。
    整句都是英文字母的語言（英文、西班牙文…）：只算長得像遊戲 ID 的（不然每個大寫開頭的字都會被問）。
    韓文、中文、日文這種：句子裡夾著的英文字母多半就是名字或 ID（「Faker 오늘 잘한다」），常見的英文字除外。"""
    out: list[str] = []
    latin = _latin_only(text)
    for m in _TOKEN.finditer(text or ""):
        word = m.group(1).strip(".-")
        if len(word) < 3 or word.lower() in _COMMON:
            continue
        if looks_like_id(word) or (not latin and len(word) >= 3 and not _VERSION.match(word) and not word.isupper()):
            if word not in out:
                out.append(word)
    return out


class NameWatch:
    """數對方的話裡每個可能是名字的字出現在幾則裡。到了 NEED 則就回傳它一次（之後不再回傳）。"""

    def __init__(self, need: int = NEED, find=None):
        """find(text) → 這句裡要數的字（不給就是 candidates：像名字的字）。縮寫提醒（textfix）也用這個數。"""
        self.need = need
        self.find = find or candidates
        self.counts: Counter = Counter()
        self.asked: set[str] = set()

    def add(self, text: str, skip=()) -> str:
        """skip：已經在不翻的字、詞彙表、或使用者說過「不是名字」的字（不分大小寫）。回傳該問的字，沒有就是空字串。"""
        skip = {str(s).lower() for s in skip}
        ask = ""
        for word in self.find(text):
            key = word.lower()
            if key in skip or key in self.asked:
                continue
            self.counts[key] += 1
            if not ask and self.counts[key] >= self.need:
                self.asked.add(key)
                self.counts.pop(key, None)
                ask = word
        if len(self.counts) > MAX_TRACKED:
            self.counts = Counter({k: v for k, v in self.counts.items() if v > 1})
        return ask


MAX_HOT = 24        # 熱詞最多幾個
MAX_HOT_CHARS = 160


def _fits(word: str, lang: str) -> bool:
    """這個詞是不是這個語言的人說話時會講出來的：英文字母的名字哪個語言都算；韓文字只給韓文，漢字、假名給中文和日文。"""
    if all(ch.isascii() for ch in word):
        return any(ch.isalpha() for ch in word)
    if lang == "ko":
        return any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in word)
    if lang in ("zh", "ja"):
        return any(0x4E00 <= ord(ch) <= 0x9FFF or 0x3040 <= ord(ch) <= 0x30FF for ch in word)
    return False


def hotwords(lang: str, words) -> str:
    """語音辨識的熱詞：只挑這個語言用得到的，太短（1 個字）、太長的不放；最多 MAX_HOT 個、MAX_HOT_CHARS 個字。"""
    out: list[str] = []
    total = 0
    for word in words:
        word = " ".join(str(word).split())
        if len(word) < 2 or len(word) > MAX_LEN or word in out or not _fits(word, lang):
            continue
        if total + len(word) + 1 > MAX_HOT_CHARS or len(out) >= MAX_HOT:
            break
        out.append(word)
        total += len(word) + 1
    return " ".join(out)
