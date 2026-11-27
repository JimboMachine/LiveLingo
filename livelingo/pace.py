"""對方講太快的提醒（2.18.0）：最近幾句辨識得不太有把握、或說得特別快的時候，
提醒使用者可以請對方慢一點，附一句對方語言的現成句子（點一下就複製）。"""

import time
from collections import deque

LOOK = 6            # 看最近幾句
UNSURE = 3          # 這幾句裡有幾句「可能聽錯」就提醒
COOLDOWN = 300.0    # 提醒過之後至少隔這麼久（秒）才再提醒
# 每秒幾個字算「很快」：中日韓一個字就是一個音節，其他語言用字母數
FAST = {"cjk": 8.0, "other": 20.0}
CJK = ("zh", "ja", "ko", "yue")

ASK = {
    "zh": "可以請你說慢一點嗎？", "zh-TW": "可以請你說慢一點嗎？", "zh-CN": "可以请你说慢一点吗？", "yue": "可唔可以講慢少少？",
    "en": "Could you speak a little slower, please?", "ko": "조금만 천천히 말해 줄 수 있어요?", "ja": "もう少しゆっくり話してもらえますか？",
    "vi": "Bạn nói chậm lại một chút được không?", "th": "ช่วยพูดช้าลงหน่อยได้ไหม", "id": "Bisa bicara sedikit lebih pelan?",
    "es": "¿Puedes hablar un poco más despacio, por favor?", "pt": "Você pode falar um pouco mais devagar, por favor?",
    "ru": "Можешь говорить чуть помедленнее, пожалуйста?", "de": "Kannst du bitte etwas langsamer sprechen?",
    "fr": "Tu peux parler un peu plus lentement, s'il te plaît ?",
}


def ask_in(lang: str) -> str:
    lang = str(lang or "")
    return ASK.get(lang) or ASK.get(lang.split("-")[0]) or ASK["en"]


def rate(text: str, length: float, lang: str) -> float:
    """每秒幾個字（不算空白和標點）。"""
    if length <= 0.3:
        return 0.0
    letters = sum(1 for ch in str(text or "") if ch.isalnum())
    return letters / length


class PaceWatch:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.recent: deque = deque(maxlen=LOOK)
        self.last_tip = -COOLDOWN

    def add(self, text: str, lang: str, unsure: bool, length: float) -> str:
        """記一句對方說的話。該提醒了就回傳「fast」或「unclear」，不然回傳空字串。"""
        family = "cjk" if str(lang or "").split("-")[0] in CJK else "other"
        fast = length >= 1.0 and rate(text, length, lang) >= FAST[family]
        self.recent.append((bool(unsure), fast))
        if len(self.recent) < LOOK // 2 or self.clock() - self.last_tip < COOLDOWN:
            return ""
        unsure_n = sum(u for u, _ in self.recent)
        fast_n = sum(f for _, f in self.recent)
        why = "unclear" if unsure_n >= UNSURE else "fast" if fast_n >= UNSURE else ""
        if why:
            self.last_tip = self.clock()
            self.recent.clear()
        return why
