"""翻譯：把文字送給本機的 llama.cpp（TranslateGemma 模型），不會把內容傳到外面。"""

import json
import re
import threading
import urllib.request
from pathlib import Path

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


def load_glossary(path: Path) -> dict[str, str]:
    """專有名詞表：每行「原文 = 譯文」，# 開頭是註解。"""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


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


def build_prompt(text: str, source: str, target: str, glossary: dict[str, str], tone: str = "casual") -> str:
    """TranslateGemma 官方的提示格式，加上聊天語氣提示和專有名詞表（實測過效果最好的組合）。"""
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
    body = (
        f"You are a professional {s_name} ({s_code}) to {t_name} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {s_name} text "
        f"while adhering to {t_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"{extra}"
        f"Produce only the {t_name} translation, without any additional explanations or commentary. "
        f"Please translate the following {s_name} text into {t_name}:\n\n\n{text}"
    )
    return f"<start_of_turn>user\n{body}<end_of_turn>\n<start_of_turn>model\n"


class Translator:
    def __init__(self, url: str, glossary_path: Path | None = None, tone: str = "casual", max_chars: int = 500):
        self.url = url.rstrip("/")
        self.glossary_path = glossary_path
        self.tone = tone
        self.max_chars = max_chars
        self._lock = threading.Lock()  # 一次只翻一則，其他排隊
        self._cache: dict[tuple[str, str], str] = {}

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        """target：zh-TW / ko / en / ja。同語言時原樣回傳。"""
        text = text.strip()[: self.max_chars]
        source = source or detect_language(text) or "en"
        if source == LANGS[target]["family"]:
            return text
        key = (text, target)
        if key in self._cache:
            return self._cache[key]
        glossary = load_glossary(self.glossary_path) if self.glossary_path else {}
        payload = {
            "prompt": build_prompt(text, source, target, glossary, self.tone),
            "max_tokens": min(1024, 64 + 4 * len(text)),
            "temperature": 0,
            "repeat_penalty": 1.0,
            "stop": ["<end_of_turn>"],
        }
        req = urllib.request.Request(
            f"{self.url}/v1/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with self._lock:
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read().decode("utf-8"))["choices"][0]["text"].strip()
        if target == "zh-TW":
            out = _to_traditional.convert(out)
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = out
        return out
