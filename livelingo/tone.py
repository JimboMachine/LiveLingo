"""語氣小圖示（2.18.0）：對方在笑、在生氣、在開玩笑、在難過的時候，翻譯旁邊加一個小圖示。
光看翻好的字常常看不出語氣（「你很厲害欸ㅋㅋ」翻成「你很厲害」就少了在笑）。
只看原文裡的記號（ㅋㅋ、www、哈哈、jk、ㅠㅠ、髒話、連續驚嘆號）和聲音大小，不另外跑模型；看不出來就不加。"""

import re

ICONS = {"laugh": "😄", "joke": "😜", "angry": "😠", "sad": "😢"}

# 浮動字幕（遊戲裡）畫不出彩色表情，改用一個短字放在句子前面
WORDS = {
    "zh-TW": {"laugh": "笑", "joke": "開玩笑", "angry": "生氣", "sad": "難過"},
    "zh-CN": {"laugh": "笑", "joke": "开玩笑", "angry": "生气", "sad": "难过"},
    "en": {"laugh": "laughing", "joke": "joking", "angry": "angry", "sad": "sad"},
    "ko": {"laugh": "웃음", "joke": "농담", "angry": "화남", "sad": "슬픔"},
    "ja": {"laugh": "笑", "joke": "冗談", "angry": "怒", "sad": "悲"},
    "vi": {"laugh": "cười", "joke": "đùa", "angry": "giận", "sad": "buồn"},
    "th": {"laugh": "หัวเราะ", "joke": "ล้อเล่น", "angry": "โกรธ", "sad": "เศร้า"},
    "id": {"laugh": "tertawa", "joke": "bercanda", "angry": "marah", "sad": "sedih"},
    "es": {"laugh": "risa", "joke": "broma", "angry": "enfado", "sad": "triste"},
    "pt": {"laugh": "risos", "joke": "brincadeira", "angry": "bravo", "sad": "triste"},
    "ru": {"laugh": "смех", "joke": "шутка", "angry": "злость", "sad": "грусть"},
    "de": {"laugh": "lacht", "joke": "Spaß", "angry": "wütend", "sad": "traurig"},
    "fr": {"laugh": "rire", "joke": "blague", "angry": "fâché", "sad": "triste"},
}

_LAUGH = re.compile(
    r"[ㅋㅎ]{2,}|(?:哈){2,}|(?:呵){2,}|(?:嘻){2,}|(?:233+)|(?<![a-z.])w{3,}(?![a-z.])|(?<![a-z])[wｗ]{2,}$|草$|（笑）|\(笑\)|【笑】|\[笑い?\]|"
    r"(?<![a-z])(?:a?ha(?:ha)+h?|he(?:he)+|lo+l|lmf?ao+|rofl|xd+|ja(?:ja)+|ka(?:ka)+|kkkk+|rs(?:rs)+|wkwk\w*|haha\w*)(?![a-z])|"
    r"555+|ха(?:ха)+|\((?:laughs?|laughing|risas?|rires?)\)|\[(?:laughter|laughs?)\]|😂|🤣|😆",
    re.IGNORECASE)
_JOKE = re.compile(
    r"(?<![a-z])(?:jk|j/k|just kidding|kidding|i'?m joking|只是開玩笑|開玩笑的?|开玩笑的?|說笑|騙你的|骗你的|농담(?:이야|이에요|입니다)?|"
    r"장난(?:이야|이에요)?|冗談(?:だよ|です)?|うそうそ|đùa thôi|giỡn thôi|ล้อเล่น|bercanda|cuma bercanda|es broma|era broma|"
    r"é brincadeira|brincadeira|шучу|шутка|nur spaß|war nur ein witz|je rigole|c'est une blague)(?![a-z])|😜|😝|😉",
    re.IGNORECASE)
_SAD = re.compile(
    r"[ㅠㅜ]{2,}|T_T|T\.T|;_;|QAQ|嗚嗚|呜呜|哭哭|泣|😢|😭|☹|:\(|\((?:sighs?|cries|crying|sobs?)\)",
    re.IGNORECASE)
_ANGRY = re.compile(
    r"(?<![a-z])(?:fuck\w*|wtf|shit|damn it|stfu|screw you|mierda|joder|puta|caralho|porra|merde|putain|scheiße|scheisse|verdammt|"
    r"блять|бля|сука|нахуй|anjing|bangsat|đm|địt|vcl)(?![a-z])|씨발|시발|ㅅㅂ|개새끼|존나|미친놈|他媽的?|他妈的?|幹你|操你|靠北|靠杯|滾開|滚开|"
    r"閉嘴|闭嘴|ふざけるな|ふざけんな|うるせぇ|うるさい|くそ|クソ|ชิบหาย|เหี้ย|😠|😡|🤬",
    re.IGNORECASE)

LOUD = 2.2      # 這句比平常大聲這麼多倍（語音）


def guess(text: str, loud: float = 0.0) -> str:
    """回傳 laugh / joke / angry / sad，看不出來回傳空字串。loud：這句比平常大聲幾倍（語音才有）。"""
    text = str(text or "").strip()
    if not text:
        return ""
    if _ANGRY.search(text):
        return "angry"
    if _JOKE.search(text):
        return "joke"
    if _LAUGH.search(text):
        return "laugh"
    if _SAD.search(text):
        return "sad"
    bangs = len(re.findall(r"[!！]", text))
    if (loud >= LOUD and bangs) or (loud >= LOUD * 1.3 and len(text) >= 4):      # 只有驚嘆號可能只是興奮，要加上大聲才算
        return "angry"
    return ""


def word(tone: str, ui_lang: str) -> str:
    return (WORDS.get(ui_lang) or WORDS["en"]).get(tone, "")
