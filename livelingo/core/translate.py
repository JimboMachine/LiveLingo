"""翻譯：把文字送給本機的 llama.cpp（TranslateGemma 模型），不會把內容傳到外面。"""

import json
import re
import threading
import unicodedata
import urllib.request

from opencc import OpenCC

# 語言代碼 → name: 顯示名稱, en: 給模型看的英文名稱, family: 用文字種類判斷出來的語言
LANGS = {
    "zh-TW": {"name": "繁體中文", "en": "Traditional Chinese", "family": "zh"},
    "zh-CN": {"name": "簡體中文", "en": "Simplified Chinese", "family": "zh"},
    "ko": {"name": "韓文", "en": "Korean", "family": "ko"},
    "en": {"name": "英文", "en": "English", "family": "en"},
    "ja": {"name": "日文", "en": "Japanese", "family": "ja"},
}
SOURCES = {
    "zh": ("Chinese", "zh"),
    "ko": ("Korean", "ko"),
    "en": ("English", "en"),
    "ja": ("Japanese", "ja"),
}

# 保險用：模型偶爾會冒出簡體字，中文輸出一律再轉成台灣繁體用語
_to_traditional = OpenCC("s2twp")
_to_simplified = OpenCC("tw2sp")


def to_script(text: str, code: str) -> str:
    """中文統一成指定的字體：zh-TW 轉成繁體（台灣用語），zh-CN 轉成簡體；其他語言原樣回傳。"""
    if code == "zh-TW":
        return _to_traditional.convert(text)
    if code == "zh-CN":
        return _to_simplified.convert(text)
    return text

HINT_INTRO = "They are talking about the game League of Legends. Use these translations for the game terms: "
# 沒有選英雄聯盟的用語包時（只有聊天縮寫）用這句
HINT_INTRO_CHAT = "They are chatting while playing an online game. Use these meanings for the chat slang: "

# 實測：少了這段，模型常常自己加「嗯、啊、哇」或表情符號，還會把「我來」翻成「幫我」之類的
FAITHFUL = ("Translate faithfully: keep the meaning, who is doing what, and the sentence type exactly as in the original. "
            "A statement stays a statement and a question stays a question. "
            "Do not add greetings, interjections or anything that is not in the original.\n")
# 模型常把這幾個時間副詞翻錯（「剛剛」翻成「現在」、「等一下」翻成「現在」），遇到時把意思告訴它
COMMON_WORDS = {
    ("zh", "ko"): [("剛剛", "방금 (a moment ago)"), ("剛才", "방금 (a moment ago)"),
                   ("等一下", "이따가 (a little later)"), ("待會", "이따가 (a little later)")],
}
# 模型固定會翻歪的句型，給它一句示範就會照著翻（實測比多寫幾句規定有用）。
# 「我們…吧」是提議，韓文應該是「…자」，沒有示範時常被翻成問句「…갈까?」；「剛剛」沒有示範時偶爾變成「그냥」。
# 每一項：(原文符合這個樣子才用, 語氣, 示範原文, 示範譯文)
BUILTIN_EXAMPLES = {
    ("zh", "ko"): [
        (re.compile(r"吧[\s。．.!！~～…]*$"), "casual", "我們走吧", "우리 가자."),
        (re.compile(r"吧[\s。．.!！~～…]*$"), "polite", "我們走吧", "우리 가요."),
        (re.compile(r"剛剛|剛才"), "casual", "我剛剛吃飽了", "나 방금 밥 다 먹었어."),
        (re.compile(r"剛剛|剛才"), "polite", "我剛剛吃飽了", "저 방금 밥 다 먹었어요."),
    ],
}
EXAMPLES_INTRO = "The user wants messages like these translated this way (source => translation):\n"
# 每個語言「朋友之間」和「有禮貌」各是哪一種說法（韓文、日文有明確的敬語體系，中文用「您、請」）
REGISTER = {
    "casual": {"ko": " (in Korean use informal 반말)", "ja": " (in Japanese use casual plain form, タメ口, not です/ます)"},
    "polite": {"ko": " (in Korean use polite 해요체)", "ja": " (in Japanese use polite です/ます form)",
               "zh-TW": " (in Chinese use polite wording such as 您 and 請)",
               "zh-CN": " (in Chinese use polite wording such as 您 and 请)"},
}
SHORT_REGISTER = {"ko": ", so use informal Korean (반말)", "ja": ", so use casual plain Japanese (タメ口)"}
# 前面幾句對話（原文）。只給模型參考代名詞、省略的主詞在說誰，不翻它們
CONTEXT_INTRO = ("For context only, these are the previous messages of the conversation. Do not translate them "
                 "and do not repeat them; use them only to understand what the new message refers to:\n")

_END_MARKS = "。．.!！?？~～…"
_ZH_QUESTION = re.compile(
    r"嗎|呢\s*$|什麼|甚麼|啥|誰|哪|怎麼|怎樣|怎麽|為什麼|為何|幾|多少|多久|是不是|有沒有|要不要|能不能|會不會|可不可以|好不好|對不對|行不行|是否")
_KO_QUESTION = re.compile(r"뭐|무슨|무엇|뭘|어디|언제|누구|누가|왜|어떻게|어때|어떤|어느|몇|얼마|"
                          r"(니|냐|까|까요|래|래요|지|죠|나요|가요|인가|는가|던가|ㄹ까|을까|나|ㄴ가)$")


def sentence_kind(text: str, source: str) -> str | None:
    """這句是直述句（statement）還是問句（question）；看不出來回傳 None。
    聊天打字和語音辨識出來的句子常常沒有標點，模型就容易把直述句猜成問句，所以先自己判斷一次。"""
    t = text.strip()
    if not t:
        return None
    if t[-1] in "?？":
        return "question"
    if source == "ko":
        # 韓文的半語（「밥 먹었어」）直述和疑問長得一樣，聊天時問句通常會打問號；沒有問號、沒有疑問詞、
        # 也不是疑問語尾的，就當成直述句
        bare = t.rstrip("。．.!！~～… ㅋㅎㅠㅜ")
        if "?" in t or "？" in t or _KO_QUESTION.search(bare):
            return None
        return "statement"
    if source != "zh":
        return "statement" if t[-1] in "。．.!！" else None
    t = _to_traditional.convert(t)      # 簡體字的「吗」「什么」也要認得
    if re.search(r"嗎\s*[。．.!！~～…]*$", t):
        return "question"
    if _ZH_QUESTION.search(t) or "?" in t or "？" in t:
        return None          # 有疑問詞但不一定是問句（「我不知道他在哪裡」），交給模型判斷
    return "statement"


def with_end_mark(text: str, source: str, kind: str | None) -> str:
    """沒有句尾標點的句子（中文、日文、韓文的直述句），補上句號或問號再給模型，它才不會把語氣猜錯。"""
    t = text.rstrip()
    if source not in ("zh", "ja", "ko") or not t or t[-1] in _END_MARKS or not kind:
        return text
    if source == "ko":
        return t + "." if kind == "statement" and t[-1] not in "ㅋㅎㅠㅜ" else text
    return t + ("？" if kind == "question" else "。")


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s。．.,，、!！?？~～…]+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def similarity(a: str, b: str) -> float:
    """兩句話有多像（0 到 1），用相鄰兩個字的重疊程度算。"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return 2 * len(x & y) / (len(x) + len(y))


def _same_sentence(a: str, b: str) -> bool:
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", t.strip().lower()).rstrip("。．.!！~～… ")
    return norm(a) == norm(b)


def detect_language(text: str) -> str | None:
    """用文字種類判斷語言：回傳 zh / ko / ja / en；None 表示沒有可翻譯的文字。"""
    hangul = kana = han = latin = 0
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            hangul += 1
        elif 0x3040 <= o <= 0x30FF:
            kana += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            han += 1
        elif ch.isalpha() and o < 0x250:
            latin += 1
    if hangul and hangul >= kana:
        return "ko"
    if kana:
        return "ja"
    if han:
        return "zh"
    if latin >= 2:
        return "en"
    return None


def apply_glossary(text: str, glossary: dict[str, str], target: str | None = None) -> tuple[str, list[str]]:
    """
    把原文裡的專有名詞直接換成指定的譯文（一次掃完，不會重複替換）。
    譯文是英文字母的（例如 Chovy）兩個方向都適用；譯文是韓文或中文的，只在翻成那個語言時才用，
    所以可以同時寫「초비 = Chovy」和「Chovy = 쵸비」。
    """
    if target:
        family = LANGS[target]["family"]
        glossary = {k: v for k, v in glossary.items() if detect_language(v) in (None, "en", family)}
    if not glossary:
        return text, []
    lower = {k.lower(): v for k, v in glossary.items()}
    pattern = re.compile("|".join(re.escape(k) for k in sorted(glossary, key=len, reverse=True)), re.IGNORECASE)
    used: list[str] = []

    def repl(m: re.Match) -> str:
        v = lower[m.group(0).lower()]
        used.append(v)
        return v

    return pattern.sub(repl, text), list(dict.fromkeys(used))


def build_prompt(text: str, source: str, target: str, glossary: dict[str, str], tone: str = "casual",
                 hints: list[tuple[str, str]] | None = None, examples: list[tuple[str, str]] | None = None,
                 style: str = "full", context: list[str] | None = None, hint_intro: str | None = None) -> str:
    """TranslateGemma 官方的提示格式，加上聊天語氣提示、專有名詞表和遊戲用語提示（實測過效果最好的組合）。
    style：full = 平常用的；short = 語氣提示換成比較短的寫法（直述句被翻成問句時重翻用）；
    plain = 只有官方的提示（模型把原文原封不動吐回來時重翻用，意思最穩但語氣會比較正式）。"""
    s_name, s_code = SOURCES[source]
    t_name = LANGS[target]["en"]
    text, kept = apply_glossary(text, glossary, target)
    if style == "plain":
        extra = ""
    elif style == "short":
        extra = ("This is a chat message between close friends"
                 + (SHORT_REGISTER.get(target, "") if tone != "polite" else "")
                 + ". Translate faithfully: a statement stays a statement, a question stays a question, "
                   "and nothing is added. Keep names, game terms and Latin-alphabet words unchanged.\n")
    elif tone == "polite":
        extra = ("The text is a chat message. Use a polite, friendly tone"
                 + REGISTER["polite"].get(target, "")
                 + ", and keep names, game terms and Latin-alphabet words unchanged.\n")
    else:
        extra = ("The text is a casual chat message between friends on Discord. Keep the same casual, friendly tone"
                 + REGISTER["casual"].get(target, "")
                 + ", and keep names, game terms and Latin-alphabet words unchanged.\n")
    if style == "full":
        extra += FAITHFUL
    if kept:
        extra += "Keep these terms exactly as written, do not translate or change them: " + "; ".join(kept) + "\n"
    hints = [(a, b) for a, b in (hints or []) if a in text]
    if hints and style != "plain":
        extra += (hint_intro or HINT_INTRO) + "; ".join(f"{a} = {b}" for a, b in hints) + "\n"
    words = [(a, b) for a, b in COMMON_WORDS.get((source, target), [])
             if a in text and not text.rstrip("。．.!！~～… ").endswith(a)]      # 單獨一句「等一下」是「等等我」，不算
    if words and style != "plain":
        extra += "Word meanings: " + "; ".join(f"{a} = {b}" for a, b in words) + "\n"
    if examples and style != "plain":
        extra += EXAMPLES_INTRO + "".join(f"{a} => {b}\n" for a, b in examples)
    if context and style == "full":
        extra += CONTEXT_INTRO + "".join(f"- {line}\n" for line in context)
    body = (
        f"You are a professional {s_name} ({s_code}) to {t_name} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {s_name} text "
        f"while adhering to {t_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"{extra}"
        f"Produce only the {t_name} translation, without any additional explanations or commentary. "
        f"Please translate the following {s_name} text into {t_name}:\n\n\n{text}"
    )
    return f"<start_of_turn>user\n{body}<end_of_turn>\n<start_of_turn>model\n"


def _leftover(out: str, source: str, target: str) -> int:
    """譯文裡還留著幾個原文的字（韓文翻成別的語言卻還有韓文字，之類的）。"""
    family = LANGS[target]["family"]
    if source == "ko" and family != "ko":
        return sum(1 for ch in out if 0xAC00 <= ord(ch) <= 0xD7A3)
    if source in ("zh", "ja") and family in ("ko", "en"):
        return sum(1 for ch in out if 0x4E00 <= ord(ch) <= 0x9FFF or 0x3040 <= ord(ch) <= 0x30FF)
    return 0


def _is_source_char(ch: str, source: str) -> bool:
    o = ord(ch)
    if source == "ko":
        return 0xAC00 <= o <= 0xD7A3
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3040 <= o <= 0x30FF


def salvage(out: str, source: str, target: str) -> str | None:
    """模型有時會先把原文抄一遍，或在譯文開頭留下「我觉得」這種兩三個沒翻的字，後面才是完整的翻譯。
    這種情況把抄的部分拿掉就是對的譯文；留下來的原文太多就不算（回傳 None），要重翻。"""
    # 「打野（정글러）」：模型把原文的詞放在括號裡當註解，拿掉括號就好
    noted = re.sub(r"\s*[（(]([^（()）]*)[)）]",
                   lambda m: "" if any(_is_source_char(ch, source) for ch in m.group(1)) else m.group(0), out).strip()
    if noted != out.strip() and len(noted) >= 2 and not _leftover(noted, source, target):
        return noted
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1].strip("()（） ").removeprefix("->").strip()
    head = 0
    while head < len(last) and (_is_source_char(last[head], source) or last[head] in " 　，,"):
        head += 1
    kept_chars = sum(1 for ch in last[:head] if _is_source_char(ch, source))
    rest = last[head:].strip()
    if kept_chars > 4 or len(rest) < max(4, kept_chars * 2) or _leftover(rest, source, target):
        return None
    return rest


_KO_LAUGH = [(re.compile(r"\s*[ㅋㅎ]{2,}"), {"zh-TW": " 哈哈", "zh-CN": " 哈哈", "en": " haha", "ja": " www"}),
             (re.compile(r"\s*[ㅠㅜ]{2,}"), {"zh-TW": " 嗚嗚", "zh-CN": " 呜呜", "en": " T_T", "ja": " 泣"})]


def tidy(out: str, target: str) -> str:
    """模型有時把韓文的「ㅋㅋ」「ㅠㅠ」原樣留在譯文裡，換成看得懂的寫法。"""
    if LANGS[target]["family"] != "ko":
        for pattern, words in _KO_LAUGH:
            out = pattern.sub(words.get(target, ""), out)
    return out.strip()


MAX_TEXT = 2000      # 一則訊息最長幾個字（介面的輸入框也是這個數）
_SENTENCE_END = re.compile(r"(?<=[。．！？!?…~～\n])|(?<=[.])(?=\s)")
_SOFT_BREAK = re.compile(r"(?<=[，、,;；:：\s])")


def split_long(text: str, limit: int) -> list[str]:
    """太長的文字切成幾段（每段不超過 limit 個字）：先在句子結尾切，一句就超過的再在逗號、空白切，真的沒地方切才硬切。"""
    def pack(pieces: list[str]) -> list[str]:
        out, now = [], ""
        for piece in pieces:
            if now and len(now) + len(piece) > limit:
                out.append(now)
                now = ""
            now += piece
        return out + ([now] if now else [])

    parts = []
    for sentence in pack([p for p in _SENTENCE_END.split(text) if p]):
        if len(sentence) <= limit:
            parts.append(sentence)
            continue
        for chunk in pack([p for p in _SOFT_BREAK.split(sentence) if p]):
            parts += [chunk[i:i + limit] for i in range(0, len(chunk), limit)]
    return [p.strip() for p in parts if p.strip()]


_DIGITS = re.compile(r"\d+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# 寫成數字但其實不是數量的網路用語：翻出來本來就不會有數字
_NUMBER_SLANG = {
    "zh": re.compile(r"(?<!\d)(?:6{2,}|5{3,}|23{2,}|88|886|520|1314|3Q|3q)(?!\d)"),
    "ko": re.compile(r"(?<!\d)(?:8282|1004)(?!\d)"),
    "ja": re.compile(r"(?<!\d)8{3,}(?!\d)"),
    "en": re.compile(r"\b(?:gr8|m8|l8r?|b4|w8|2day|2nite|2moro|4ever|n1)\b", re.I),
}
_HAN = "零一二三四五六七八九"
_KO_SINO = "영일이삼사오육칠팔구"
_KO_NATIVE = {1: ("하나", "한", "첫"), 2: ("둘", "두"), 3: ("셋", "세", "석"), 4: ("넷", "네", "넉"), 5: ("다섯",), 6: ("여섯",),
              7: ("일곱",), 8: ("여덟",), 9: ("아홉",), 10: ("열",), 20: ("스물", "스무")}
_EN = {0: ("zero", "no"), 1: ("one", "first", "a", "an", "once"), 2: ("two", "second", "twice", "both", "double"),
       3: ("three", "third", "triple"), 4: ("four", "fourth"), 5: ("five", "fifth"), 6: ("six", "sixth"), 7: ("seven", "seventh"),
       8: ("eight", "eighth"), 9: ("nine", "ninth"), 10: ("ten", "tenth"), 11: ("eleven",), 12: ("twelve",), 20: ("twenty",)}


def numbers(text: str, source: str | None = None) -> list[str]:
    """文字裡用阿拉伯數字寫的數（全形算半形、1,000 算 1000、06 算 6）。「666」「gr8」這種用語不算。"""
    text = unicodedata.normalize("NFKC", text)
    if source in _NUMBER_SLANG:
        text = _NUMBER_SLANG[source].sub(" ", text)
    return [str(int(n)) for n in _DIGITS.findall(_THOUSANDS.sub("", text))]


def _spelled(n: int, target: str) -> list[str]:
    """這個數在譯文裡不用阿拉伯數字時可能的寫法（只處理常見的小數字和整千、整萬）。"""
    family, out = LANGS[target]["family"], []
    if family in ("zh", "ja"):
        if n < 10:
            out.append(_HAN[n])
        elif n < 100:
            out.append(("" if n < 20 else _HAN[n // 10]) + "十" + (_HAN[n % 10] if n % 10 else ""))
        out += {0: ["〇", "沒", "没", "無", "无", "ゼロ"], 2: ["兩", "两", "雙", "双", "ふた"], 1: ["ひと", "首"], 30: ["半小時", "半小时", "半"],
                100: ["百"]}.get(n, [])
        if n >= 1000 and n % 1000 == 0:
            out += [f"{n // 1000}千", "千"] if n < 10000 else []
        if n >= 10000 and n % 10000 == 0:
            out += [f"{n // 10000}萬", f"{n // 10000}万"]
    elif family == "ko":
        if n < 10:
            out.append(_KO_SINO[n])
        elif n < 100:
            out.append(("" if n < 20 else _KO_SINO[n // 10]) + "십" + (_KO_SINO[n % 10] if n % 10 else ""))
        out += _KO_NATIVE.get(n, ())
        if 10 < n < 30 and n % 10 in _KO_NATIVE:
            out += [("열" if n < 20 else "스물") + w for w in _KO_NATIVE[n % 10]]
        out += {0: ["공", "없"], 30: ["반"], 100: ["백"]}.get(n, [])
        if n >= 1000 and n % 1000 == 0 and n < 10000:
            out += [f"{n // 1000}천", "천"]
        if n >= 10000 and n % 10000 == 0:
            out.append(f"{n // 10000}만")
    else:
        out += _EN.get(n, ())
        out += {30: ["half"], 100: ["hundred"], 1000: ["thousand"]}.get(n, [])
    if n >= 1000 and n % 1000 == 0:
        out.append(f"{n // 1000}k")
    return out


def numbers_lost(original: str, out: str, target: str, source: str | None = None) -> list[str]:
    """
    原文裡用數字寫的數，譯文裡找不到的（譯文寫成「三」「세」「three」也算有）。
    時間、人數、金額翻錯一個數字意思就差很多，而且看譯文看不出來，所以翻完核對一次。
    只查「原文有、譯文沒有」：譯文把「세 명」寫成「3 個人」是對的，不算錯。
    """
    source = source or detect_language(original)
    if not out or source == LANGS[target]["family"]:
        return []
    if source is None:      # 整句只有數字和符號，看不出語言：只要是哪一種語言的用語就不查
        original = unicodedata.normalize("NFKC", original)
        for slang in _NUMBER_SLANG.values():
            original = slang.sub(" ", original)
    have = numbers(out)
    text = unicodedata.normalize("NFKC", out)
    words = set(re.findall(r"[a-z]+", text.lower()))
    lost = []
    for n in numbers(original, source):
        if n in have:
            have.remove(n)
        elif not any((w in words) if w.isascii() and w.isalpha() else (w.lower() in text.lower()) for w in _spelled(int(n), target)):
            lost.append(n)
    return lost


def why(ex: BaseException) -> str:
    """翻譯失敗的原因，分成幾種（介面用使用者的語言說明；電腦回報的原文只當補充）。"""
    import socket
    import urllib.error

    if isinstance(ex, urllib.error.HTTPError):
        return "engine_key" if ex.code in (401, 403) else "engine_busy" if ex.code in (429, 503) else "engine_reply"
    if isinstance(ex, (TimeoutError, socket.timeout)) or "timed out" in str(ex).lower():
        return "timeout"
    if isinstance(ex, (urllib.error.URLError, ConnectionError)):
        return "engine_down"
    if isinstance(ex, (ValueError, KeyError, IndexError)):
        return "engine_reply"      # 引擎回了看不懂的東西
    return "other"


class Translator:
    def __init__(self, url: str, glossary=None, tone: str = "casual", max_chars: int = 500, hints=None, lessons=None):
        """glossary：呼叫後回傳 {原文: 譯文}；hints：hints(原文, 來源語言, 目標語言) 回傳 [(詞, 建議譯法)]；
        lessons：呼叫後回傳使用者教過的句子 [{"src", "dst", "lang", "target"}]。"""
        self.url = url.rstrip("/")
        self.key = ""                     # 翻譯引擎這一次啟動的金鑰（Engine.key）；空的就不帶
        self.glossary = glossary or (lambda: {})
        self.hints = hints or (lambda text, source, target: [])
        self.lessons = lessons or (lambda: [])
        self.tone = tone
        self.hint_intro = HINT_INTRO      # 用語提示前面那句話（照選了哪些用語包換）
        self.max_chars = max_chars
        self._lock = threading.Lock()  # 一次只翻一則，其他排隊
        self._cache: dict[tuple, str] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _ask(self, text: str, source: str, target: str, hints: list, examples: list | None = None,
             style: str = "full", context: list[str] | None = None) -> str:
        payload = {
            "prompt": build_prompt(text, source, target, self.glossary(), self.tone, hints, examples, style, context,
                                   self.hint_intro),
            "max_tokens": min(1024, 64 + 4 * len(text)),
            "temperature": 0,
            "repeat_penalty": 1.0,
            "stop": ["<end_of_turn>"],
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            f"{self.url.rstrip('/')}/v1/completions", data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))["choices"][0]["text"].strip()

    def taught(self, text: str, source: str, target: str) -> tuple[str | None, list[tuple[str, str]]]:
        """使用者教過的句子：一模一樣的直接回傳他要的翻法；很像的最多挑兩句當範例。"""
        scored = []
        for item in self.lessons():
            if item.get("target") != target or item.get("lang") != source:
                continue
            if _same_sentence(item["src"], text):
                return item["dst"], []
            score = similarity(item["src"], text)
            if score >= 0.5:
                scored.append((score, item["src"], item["dst"]))
        scored.sort(reverse=True)
        found = [(src, dst) for _, src, dst in scored[:2]]
        plain = _to_traditional.convert(text) if source == "zh" else text
        for pattern, tone, src, dst in BUILTIN_EXAMPLES.get((source, target), []):
            if len(found) < 2 and tone == self.tone and pattern.search(plain):
                found.append((src, dst))
        return None, found

    def _clean(self, out: str, text: str, source: str, target: str, hints: list, examples: list) -> str:
        """譯文裡還留著原文的字：先試著把模型抄的原文拿掉，不行就換寫法重翻，挑最乾淨的。"""
        if not _leftover(out, source, target):
            return out
        fixed = salvage(out, source, target)
        if fixed:
            return fixed
        best = out
        attempts = [("short", hints, examples)] + ([("full", [], examples)] if hints else []) + [("plain", [], [])]
        for style, h, ex in attempts:
            other = self._ask(text, source, target, h, ex, style)
            other = salvage(other, source, target) or other if _leftover(other, source, target) else other
            if _leftover(other, source, target) < _leftover(best, source, target):
                best = other
            if not _leftover(best, source, target):
                break
        return best

    def translate(self, text: str, target: str, source: str | None = None, context: list[str] | None = None) -> str:
        """target：zh-TW / zh-CN / ko / en / ja。原文已經是那個語言時不用翻，中文只做繁簡轉換。
        context：前面幾句對話的原文（最多用最後兩句），只給模型參考，不會被翻出來。"""
        text = text.strip()[:MAX_TEXT]
        source = source or detect_language(text) or "en"
        if source == LANGS[target]["family"]:
            return to_script(text, target)
        if len(text) > self.max_chars:
            # 太長：以前是悄悄只翻前 500 個字，後面的不見了也沒人知道。現在切成幾段分開翻，再接起來
            parts = split_long(text, self.max_chars)
            outs = [self.translate(part, target, source, context if i == 0 else None) for i, part in enumerate(parts)]
            return ("" if LANGS[target]["family"] in ("zh", "ja") else " ").join(o for o in outs if o)
        context = [c.strip()[:120] for c in (context or []) if c and c.strip()][-2:]
        key = (text, target, *context)
        if key in self._cache:
            return self._cache[key]
        exact, examples = self.taught(text, source, target)
        if exact:
            return exact
        hints = self.hints(text, source, target)
        kind = sentence_kind(text, source)
        sent = with_end_mark(text, source, kind)
        with self._lock:
            out = self._ask(sent, source, target, hints, examples, context=context)
            if context and len(out) > 3 * len(sent) + 30:
                # 模型把前面幾句也翻進來了（譯文長得不合理）：不給前文再翻一次
                out = self._ask(sent, source, target, hints, examples)
            out = self._clean(out, sent, source, target, hints, examples)
            if kind == "statement" and ("?" in out or "？" in out):
                # 直述句被翻成問句：換一種寫法再翻一次，那次不是問句就用那次的
                again = self._ask(sent, source, target, hints, examples, "short")
                if "?" not in again and "？" not in again and not _leftover(again, source, target):
                    out = again
            if numbers_lost(sent, out, target, source):
                # 原文的數字在譯文裡不見了或變了：換寫法再翻，哪一次數字都在就用哪一次。都不行就照舊，由畫面提醒使用者核對
                for style in ("short", "plain"):
                    again = self._ask(sent, source, target, hints if style == "short" else [], examples if style == "short" else [], style)
                    again = self._clean(again, sent, source, target, [], []) if _leftover(again, source, target) else again
                    if not numbers_lost(sent, again, target, source) and not _leftover(again, source, target):
                        out = again
                        break
        out = to_script(out, target)
        out = tidy(out, target)
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = out
        return out
