"""喊我名字提醒（2.11.0）：對方的話裡出現我的名字或我設的關鍵字，就在那一則上標出來、響一聲。只在這台電腦上比對文字。"""

import re

MAX_WORDS = 20
MAX_LEN = 30


def clean_words(raw) -> list[str]:
    """設定裡的關鍵字：可以是一串用逗號、頓號、換行分開的字，或一個清單。去掉空的和重複的（不分大小寫）。"""
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


def _pattern(word: str) -> re.Pattern:
    """英文、數字的名字要整個字才算（Jim 不算在 Jimbo 裡）；中日韓文字沒有空格，包含就算。"""
    body = re.escape(word)
    if word.isascii() and re.match(r"\w", word[0]) and re.match(r"\w", word[-1]):
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, re.IGNORECASE)


def find(texts, words: list[str]) -> str:
    """texts（原文、譯文）裡第一個出現的關鍵字；沒有就是空字串。"""
    for word in words:
        pattern = _pattern(word)
        if any(pattern.search(str(t or "")) for t in texts):
            return word
    return ""
