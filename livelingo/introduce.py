"""聽到自報名字就建議記住聲音（2.18.0）：對方說「我是 Mina」「I'm Mina」「저는 민아예요」時，
問使用者要不要把這個說話者的聲音存成 Mina。只是建議，按了「記住」才會存。"""

import re

_NAME = r"([A-Z][a-zA-Z'\-]{1,15})"
_PATTERNS = {
    "zh": [r"(?:我是|我叫|我的名字是|叫我)\s*([A-Za-z][A-Za-z'\-]{1,15}|[一-鿿]{2,3})(?=[\s，。,.!！?？啦喔哦呀啊]|$)"],
    "en": [r"\b(?:I'm|I am|my name is|my name's|this is|call me|it's)\s+" + _NAME + r"\b"],
    "ko": [r"(?:저는|나는|난|제 이름은|내 이름은)\s*([가-힣]{2,4}|[A-Za-z]{2,15})\s*(?:이에요|예요|입니다|이야|야|라고 해요|라고 합니다|이라고 해요)"],
    "ja": [r"(?:私は|わたしは|僕は|ぼくは|俺は|おれは)\s*([ァ-ヶー]{2,8}|[A-Za-z]{2,15}|[一-鿿]{2,4})(?:です|だよ|って言います|と申します|といいます)",
           r"([ァ-ヶー]{2,8}|[A-Za-z]{2,15})(?:と申します|といいます|って言います)"],
    "es": [r"\b(?:me llamo|mi nombre es|soy)\s+" + _NAME + r"\b"],
    "pt": [r"\b(?:meu nome é|me chamo|eu sou o|eu sou a|sou o|sou a)\s+" + _NAME + r"\b"],
    "fr": [r"\b(?:je m'appelle|je suis|moi c'est|c'est)\s+" + _NAME + r"\b"],
    "de": [r"\b(?:ich heiße|ich heisse|mein name ist|ich bin der|ich bin die|ich bin)\s+" + _NAME + r"\b"],
    "ru": [r"(?:меня зовут|я\s*[-—–]?\s*)\s*([А-ЯЁ][а-яё]{1,15}|[A-Z][a-zA-Z]{1,15})\b"],
    "vi": [r"\b(?:tôi là|tên tôi là|mình là|em là|anh là|tên mình là)\s+([A-ZĐ][\wÀ-ỹ]{1,15})"],
    "th": [r"(?:ผมชื่อ|ฉันชื่อ|หนูชื่อ|เราชื่อ|ชื่อ)\s*([฀-๿]{2,10}|[A-Za-z]{2,15})"],
    "id": [r"\b(?:nama saya|namaku|nama aku|saya|aku)\s+" + _NAME + r"\b"],
}
_COMPILED = {lang: [re.compile(p, re.IGNORECASE if lang in ("ko", "zh", "ja", "th") else 0) for p in ps] for lang, ps in _PATTERNS.items()}

# 看起來像名字、其實是常見的字（I'm Sorry、저는 학생이에요、我是說…）
_NOT_NAMES = {
    "sorry", "fine", "good", "okay", "ok", "here", "back", "ready", "done", "sure", "not", "just", "going", "so", "very", "really",
    "the", "a", "an", "in", "on", "at", "from", "with", "out", "home", "tired", "hungry", "busy", "new", "late", "coming", "playing",
    "gonna", "trying", "still", "also", "already", "right", "alright", "free", "listening", "talking", "there", "what", "that", "this",
    "it", "me", "you", "your", "my", "his", "her", "their", "our", "god", "dead", "lost", "stuck", "afk", "mute", "muted",
    "학생", "괜찮", "여기", "지금", "한국", "사람", "친구", "회사원", "선생님", "게이머", "초보", "고수", "남자", "여자",
    "說", "说", "覺得", "觉得", "想", "要", "在", "不是", "一個", "一个", "台灣人", "台湾人", "中國人", "中国人", "學生", "学生",
    "老師", "老师", "新手", "玩家", "男生", "女生", "大丈夫", "学生", "日本人", "初心者",
}


def name_in(text: str, lang: str) -> str:
    """這句話裡有沒有自報名字；有就回傳名字，沒有回傳空字串。lang：這句話的語言代碼（zh、en、ko…）。"""
    text = str(text or "")
    family = "zh" if str(lang or "").startswith("zh") or lang == "yue" else str(lang or "")
    for pattern in _COMPILED.get(family, []) + ([] if family == "en" else _COMPILED["en"]):
        m = pattern.search(text)
        if not m:
            continue
        name = m.group(1).strip(" '-")
        if len(name) < 2 or name.lower() in _NOT_NAMES or any(name.startswith(w) for w in _NOT_NAMES if len(w) >= 2 and not w.isascii()):
            continue
        return name[:24]
    return ""
