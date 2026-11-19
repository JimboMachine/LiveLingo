"""翻譯：把文字送給本機的 llama.cpp（TranslateGemma 模型），不會把內容傳到外面。"""

import json
import re
import threading
import urllib.request

from opencc import OpenCC

# 語言代碼 → name: 顯示名稱, en: 給模型看的英文名稱, family: 用文字種類判斷出來的語言
LANGS = {
    "zh-TW": {"name": "繁體中文", "en": "Traditional Chinese", "family": "zh"},
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

HINT_INTRO = "They are talking about the game League of Legends. Use these translations for the game terms: "


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
                 hints: list[tuple[str, str]] | None = None) -> str:
    """TranslateGemma 官方的提示格式，加上聊天語氣提示、專有名詞表和遊戲用語提示（實測過效果最好的組合）。"""
    s_name, s_code = SOURCES[source]
    t_name = LANGS[target]["en"]
    text, kept = apply_glossary(text, glossary, target)
    if tone == "polite":
        extra = ("The text is a chat message. Use a polite, friendly tone"
                 + (" (in Korean use polite 해요체)" if target == "ko" else "")
                 + ", and keep names, game terms and Latin-alphabet words unchanged.\n")
    else:
        extra = ("The text is a casual chat message between friends on Discord. Keep the same casual, friendly tone"
                 + (" (in Korean use informal 반말)" if target == "ko" else "")
                 + ", and keep names, game terms and Latin-alphabet words unchanged.\n")
    if kept:
        extra += "Keep these terms exactly as written, do not translate or change them: " + "; ".join(kept) + "\n"
    hints = [(a, b) for a, b in (hints or []) if a in text]
    if hints:
        extra += HINT_INTRO + "; ".join(f"{a} = {b}" for a, b in hints) + "\n"
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


class Translator:
    def __init__(self, url: str, glossary=None, tone: str = "casual", max_chars: int = 500, hints=None):
        """glossary：呼叫後回傳 {原文: 譯文}；hints：hints(原文, 來源語言, 目標語言) 回傳 [(詞, 建議譯法)]。"""
        self.url = url.rstrip("/")
        self.glossary = glossary or (lambda: {})
        self.hints = hints or (lambda text, source, target: [])
        self.tone = tone
        self.max_chars = max_chars
        self._lock = threading.Lock()  # 一次只翻一則，其他排隊
        self._cache: dict[tuple[str, str], str] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _ask(self, text: str, source: str, target: str, hints: list) -> str:
        payload = {
            "prompt": build_prompt(text, source, target, self.glossary(), self.tone, hints),
            "max_tokens": min(1024, 64 + 4 * len(text)),
            "temperature": 0,
            "repeat_penalty": 1.0,
            "stop": ["<end_of_turn>"],
        }
        req = urllib.request.Request(
            f"{self.url.rstrip('/')}/v1/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))["choices"][0]["text"].strip()

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        """target：zh-TW / ko / en / ja。同語言時原樣回傳。"""
        text = text.strip()[: self.max_chars]
        source = source or detect_language(text) or "en"
        if source == LANGS[target]["family"]:
            return text
        key = (text, target)
        if key in self._cache:
            return self._cache[key]
        hints = self.hints(text, source, target)
        with self._lock:
            out = self._ask(text, source, target, hints)
            if hints and _leftover(out, source, target):
                # 加了用語提示之後偶爾會有一段沒翻到（原文的字直接留在譯文裡），這時不用提示重翻一次，挑比較乾淨的
                plain = self._ask(text, source, target, [])
                if _leftover(plain, source, target) < _leftover(out, source, target):
                    out = plain
        if target == "zh-TW":
            out = _to_traditional.convert(out)
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = out
        return out
