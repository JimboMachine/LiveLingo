"""翻譯前的小修補（2.15.0）：被切成兩半的句子接回去、螢幕讀錯的字修正、只有語助詞的句子不翻、看不懂的縮寫提醒。
全部只在這台電腦上比對文字，不用翻譯引擎。"""

import re

from .names import _COMMON, NameWatch

# ---- 8. 斷句修補：一句話被語音切成兩半（中間停頓了一下）時，後半句接回前半句再翻

JOIN_GAP = 1.5       # 後半句在前半句之後幾秒內開始說，才算同一句
JOIN_SHORT = 12      # 前半句最多幾個字（中文、日文、韓文算字，其他算詞）才可能是沒說完的
_END = re.compile(r"[.!?。！？…~～♪」』)）\]]\s*$")
_NO_SPACES = ("zh", "ja")


def _size(text: str, lang: str | None) -> int:
    if lang in _NO_SPACES or lang == "ko":
        return len(re.sub(r"[\s\W_]+", "", text))
    return len(text.split())


def should_join(prev: str, text: str, gap: float, lang: str | None) -> bool:
    """前半句 prev 是不是沒說完、後半句 text 該接上去。gap：後半句開始說的時候，前半句結束了幾秒。
    前半句有句號、問號，或很長，就是說完了；後半句很長也不接（是新的一句）。"""
    prev, text = (prev or "").strip(), (text or "").strip()
    if not prev or not text or gap < 0 or gap > JOIN_GAP or _END.search(prev):
        return False
    return _size(prev, lang) <= JOIN_SHORT and _size(prev, lang) + _size(text, lang) <= JOIN_SHORT * 3


def join(prev: str, text: str, lang: str | None) -> str:
    sep = "" if lang in _NO_SPACES else " "
    return prev.strip().rstrip(",，、") + sep + text.strip()


# ---- 9. 螢幕讀錯字修正：數字裡夾著的 O、l、I（1O:3O → 10:30），韓文子音旁邊的 0、o、7（0ㅋ → ㅇㅋ）

_NUMBERISH = re.compile(r"(?<![A-Za-z])[0-9OolI|]{2,}(?![A-Za-z])")
_TO_DIGIT = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})
_JAMO = "ㄱ-ㅎ"
_NEAR_JAMO = re.compile(rf"(?<=[{_JAMO}])[0oO]|[0oO](?=[{_JAMO}])")
_SEVEN_JAMO = re.compile(rf"(?<=[{_JAMO}])7|7(?=[{_JAMO}])")


def fix_ocr(text: str) -> str:
    """讀螢幕常見的讀錯：只改看得出來一定是讀錯的地方（數字中間的字母、韓文子音縮寫裡的數字），其他不動。"""
    def numbers(m: re.Match) -> str:
        run = m.group(0)
        digits = sum(ch.isdigit() for ch in run)
        return run.translate(_TO_DIGIT) if digits and digits * 2 >= len(run) else run

    text = _NUMBERISH.sub(numbers, text or "")
    text = re.sub(r"(?<=\d)[Oo](?=[:.,]?\d)|(?<=\d[:.,])[Oo]", "0", text)      # 1:O5、2.O
    text = _NEAR_JAMO.sub("ㅇ", text)
    return _SEVEN_JAMO.sub("ㄱ", text)


# ---- 38. 語助詞：整句只有「嗯」「啊」「呃」、笑聲，不出字幕、不翻

_FILLERS = sorted(set("""
嗯 恩 啊 阿 呃 喔 哦 噢 欸 誒 唉 哎 哈 呵 嘿 嘻 咦 嗯哼 欸嘿 唔 呣
음 어 아 으 흠 하 허 헤 히 ㅋ ㅎ 엄 에이
um umm uh uhh erm er ah ahh oh ohh hmm hm mm mmm ha he hah heh huh eh ehh
えー えっと えと あの あのー うーん ああ あー はは ふふ へへ ほほ ん んー まあ
""".split()), key=len, reverse=True)
_FILLER_RUN = re.compile(r"(?:" + "|".join(re.escape(f) for f in _FILLERS) + r")+", re.IGNORECASE)
_LAUGH_MARK = re.compile(r"[\(\[（【*]\s*(?:笑|笑聲|웃음|laughs?|laughter|laughing|笑い)\s*[\)\]）】*]", re.IGNORECASE)


def only_filler(text: str) -> bool:
    """整句都是語助詞、笑聲（還有標點、拉長音）：True。只要有一個真的字就是 False。"""
    s = _LAUGH_MARK.sub(" ", text or "")
    s = re.sub(r"[\s\W_ー〜~]+", "", s)
    return not s or bool(_FILLER_RUN.fullmatch(s))


# ---- 12. 看不懂的縮寫：同一個縮寫在對方的話裡出現 ABBR_NEED 次、又不在詞彙表和用語包裡，問使用者要不要補意思

ABBR_NEED = 5
_KO_ABBR = re.compile(r"(?<![ㄱ-ㅎ])[ㄱ-ㅎ]{2,4}(?![ㄱ-ㅎ])")
_LATIN_ABBR = re.compile(r"(?<![\w])([A-Za-z]{2,5})(?![\w])")


def abbreviations(text: str) -> list[str]:
    """這句話裡像縮寫的：韓文只有子音的（ㅈㅂ、ㄱㅊ），全大寫的英文字（GLHF、AFK 這種常見的除外）。
    一樣的子音連著（ㅋㅋ、ㅎㅎ、ㅠㅠ）是笑、哭，不算。"""
    out: list[str] = []
    for m in _KO_ABBR.finditer(text or ""):
        word = m.group(0)
        if len(set(word)) > 1 and word not in out:
            out.append(word)
    for m in _LATIN_ABBR.finditer(text or ""):
        word = m.group(1)
        if word.isupper() and word.lower() not in _COMMON and word not in out:
            out.append(word)
    return out


def abbr_watch() -> NameWatch:
    return NameWatch(need=ABBR_NEED, find=abbreviations)
