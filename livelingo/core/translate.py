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
    "vi": {"name": "越南文", "en": "Vietnamese", "family": "vi"},
    "th": {"name": "泰文", "en": "Thai", "family": "th"},
    "id": {"name": "印尼文", "en": "Indonesian", "family": "id"},
    "es": {"name": "西班牙文", "en": "Spanish", "family": "es"},
    "pt": {"name": "葡萄牙文", "en": "Portuguese", "family": "pt"},
    "ru": {"name": "俄文", "en": "Russian", "family": "ru"},
    "de": {"name": "德文", "en": "German", "family": "de"},
    "fr": {"name": "法文", "en": "French", "family": "fr"},
}
SOURCES = {
    "zh": ("Chinese", "zh"),
    "ko": ("Korean", "ko"),
    "en": ("English", "en"),
    "ja": ("Japanese", "ja"),
    "vi": ("Vietnamese", "vi"),
    "th": ("Thai", "th"),
    "id": ("Indonesian", "id"),
    "es": ("Spanish", "es"),
    "pt": ("Portuguese", "pt"),
    "ru": ("Russian", "ru"),
    "de": ("German", "de"),
    "fr": ("French", "fr"),
}
# 用拉丁字母寫的語言：只看文字種類分不出來，要再看常用字（見 latin_language）
LATIN = ("en", "es", "pt", "fr", "de", "id", "vi")
# 字和字之間不空格的語言：切長句、接回來的時候不用補空格
NO_SPACES = ("zh", "ja", "th")

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
            "Do not add greetings, interjections or anything that is not in the original.\n"
            "Keep every name, place, number and fact exactly as in the original; never swap them for ones from "
            "the reader's country. Do not explain, comment, guess the reason or add words of your own.\n")
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
# 台灣的地名：前面幾句在講台灣、對方是韓國人時，實測 12B 會把「我是台北人」翻成「나는 서울 사람」（首爾人），
# 規定寫在提示裡也擋不住；把地名是哪裡告訴它就不會換掉。用英文寫，翻成哪種語言都適用。
# 不放「台灣」：模型本來就翻得對，放了 4B 反而會把「臺灣」兩個字照抄進韓文
PLACES = {"台北": "Taipei (a city in Taiwan)", "新北": "New Taipei (a city in Taiwan)",
          "桃園": "Taoyuan (a city in Taiwan)", "新竹": "Hsinchu (a city in Taiwan)", "台中": "Taichung (a city in Taiwan)",
          "台南": "Tainan (a city in Taiwan)", "高雄": "Kaohsiung (a city in Taiwan)", "基隆": "Keelung (a city in Taiwan)",
          "宜蘭": "Yilan (in Taiwan)", "花蓮": "Hualien (in Taiwan)", "台東": "Taitung (in Taiwan)",
          "屏東": "Pingtung (in Taiwan)", "嘉義": "Chiayi (a city in Taiwan)", "彰化": "Changhua (in Taiwan)",
          "墾丁": "Kenting (in Taiwan)", "澎湖": "Penghu (islands of Taiwan)", "金門": "Kinmen (islands of Taiwan)"}
PLACES_INTRO = "Place names (keep them as these places): "
# 台灣口語裡模型常翻錯意思的詞（使用者紀錄裡真的翻錯的）：「我很色」翻成「我很害羞」「很華麗」
# 意思用目標語言的說法給（只給英文說明時，12B 還是翻成「수작이 심하다」「색하다」）；沒列的語言用英文
ZH_SLANG = [(re.compile(r"[很好超太蠻滿有點比較]色(?!彩|調|系|號)|色色|色情"), "色",
             {"ko": "야하다, 밝히다 (pervy, horny)", "ja": "エロい、スケベ (pervy)", "en": "pervy, horny",
              "": "pervy, horny (sexual meaning; not shy, not colorful)"})]


# 模型看到拉丁字母會照抄（提示裡說了名字和英文字不要翻）：「A片」會原樣留在韓文裡，再被當成沒翻好重翻成很正式的句子。
# 送進模型前先換成一般的說法（只換給模型看的那份，記住的翻法還是用原文當索引）
ZH_PLAIN = [(re.compile(r"[Aa]片"), "成人影片")]


def plain_zh(text: str) -> str:
    for pattern, word in ZH_PLAIN:
        text = pattern.sub(word, text)
    return text


def slang_in(text: str, target: str = "") -> list[tuple[str, str]]:
    """原文（中文）裡模型常翻錯的口語：[(詞, 用目標語言說的意思)]。"""
    plain = _to_traditional.convert(text)
    family = LANGS[target]["family"] if target in LANGS else ""
    return [(label, meaning.get(family, meaning[""])) for pattern, label, meaning in ZH_SLANG if pattern.search(plain)]


def places_in(text: str) -> list[tuple[str, str]]:
    """原文（中文）裡提到的台灣地名：[(原文寫法, 英文和是哪裡)]。臺／台、簡體都認得。"""
    plain = _to_traditional.convert(text).replace("臺", "台")
    return [(name, where) for name, where in PLACES.items() if name in plain]


EXAMPLES_INTRO = "The user wants messages like these translated this way (source => translation):\n"
# 每個語言「朋友之間」和「有禮貌」各是哪一種說法（韓文、日文有明確的敬語體系，中文用「您、請」）
REGISTER = {
    "casual": {"ko": " (in Korean use informal 반말)", "ja": " (in Japanese use casual plain form, タメ口, not です/ます)",
               "es": " (in Spanish address the reader as tú)", "pt": " (in Portuguese address the reader as você, casual Brazilian style)",
               "fr": " (in French address the reader as tu)", "de": " (in German address the reader as du)",
               "ru": " (in Russian address the reader as ты)", "th": " (in Thai use casual speech without ครับ/ค่ะ)",
               "vi": " (in Vietnamese use casual speech between friends)", "id": " (in Indonesian use casual chat style)"},
    "polite": {"ko": " (in Korean use polite 해요체)", "ja": " (in Japanese use polite です/ます form)",
               "zh-TW": " (in Chinese use polite wording such as 您 and 請)",
               "zh-CN": " (in Chinese use polite wording such as 您 and 请)",
               "es": " (in Spanish address the reader as usted)", "pt": " (in Portuguese use polite wording)",
               "fr": " (in French address the reader as vous)", "de": " (in German address the reader as Sie)",
               "ru": " (in Russian address the reader as Вы)", "th": " (in Thai use polite particles ครับ/ค่ะ)",
               "vi": " (in Vietnamese use polite wording)", "id": " (in Indonesian use polite wording)"},
}
SHORT_REGISTER = {"ko": ", so use informal Korean (반말)", "ja": ", so use casual plain Japanese (タメ口)",
                  "es": ", so use tú", "fr": ", so use tu", "de": ", so use du", "ru": ", so use ты"}
# 前面幾句對話（原文）。只給模型參考代名詞、省略的主詞在說誰，不翻它們。
# 2.10.1 換了寫法：舊的「For context only… Do not translate them」實測 12B 還是會把前一句的意思接在譯文後面
# （前一句「…我雖然溫柔，但比較色」、這一句「…我雖然溫柔」→ 後面多了「我很害羞」）。這個寫法加上空一行，同樣的句子就不會了
CONTEXT_INTRO = "Earlier messages in this chat (context only, never translate or repeat them):\n"

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


# 拉丁字母的語言靠常用字分辨（聊天的句子很短，看常用字比看字母頻率準）。分不出來時當成英文。
_LATIN_WORDS = {
    "en": "the and is are you i to a of it that in we go my me lets let's what this yes ok have be for with on not can just they "
          "he she im i'm gg was will do don't dont your our there no",
    "es": "que el la los las y es por para con una un está esta estoy vamos pero como muy bien ya aquí aqui eso esto jaja "
          "también tambien qué tengo hay yo tú mi del al se lo le si sí nada ahora vale oye",
    "pt": "que o os não nao é eu você voce vc uma um com para pra mas muito tá ta isso aqui ele ela vamos bora kkk kkkk mano "
          "cara tem tô to também tambem do da no na meu minha agora sim beleza",
    "fr": "le la les des et est je tu il on pas ne un une que qui c'est j'ai pour avec mais oui non très tres ça ca vous nous "
          "mdr allez du au sur suis y'a fait bien",
    "de": "der die das und ist ich du nicht ein eine zu mit auf den dem es wir ihr ja nein aber mal schon auch noch was wie "
          "geht gut bitte danke bin hab habe los jetzt",
    "id": "yang dan di ke ini itu aku kamu gw gue lu tidak gak ga nggak sudah udah ada mau bisa ayo dong sih aja juga apa "
          "wkwk wkwkwk banget lagi sama buat kita mana nih deh kok",
    "vi": "không khong ko đi di tôi toi bạn ban là co có này nay thì thi rồi roi nhé nhe ơi oi mình minh dc được duoc vs "
          "với voi anh em chưa chua đâu dau lên len đánh danh thôi thoi đó do",
}
_LATIN_WORDS = {k: set(v.split()) for k, v in _LATIN_WORDS.items()}
# 只有那個語言才會用到的字母，看到一個就很有把握
_LATIN_LETTERS = {"es": "ñ¿¡", "pt": "ãõ", "fr": "œèëîïÿ", "de": "äöüß", "vi": "ơưăđ"}


def latin_language(text: str) -> str:
    """拉丁字母寫的句子是哪一種語言：en / es / pt / fr / de / id / vi。看不出來就當英文。"""
    low = text.lower()
    if any(0x1EA0 <= ord(ch) <= 0x1EF9 for ch in low):      # 越南文的聲調字母（ạ ả ế ộ …）
        return "vi"
    words = re.findall(r"[a-zà-ÿœßăđơư']+", low)
    score = {k: sum(1 for w in words if w in v) for k, v in _LATIN_WORDS.items()}
    for k, letters in _LATIN_LETTERS.items():
        score[k] += 3 * sum(low.count(ch) for ch in letters)
    if "ç" in low:
        score["pt"] += 1
        score["fr"] += 1
    best = max(score.values())
    if best == 0:
        return "en"
    order = ["en"] + [k for k in score if k != "en"]      # 平手時英文優先
    return max(order, key=lambda k: score[k])


def detect_language(text: str) -> str | None:
    """用文字種類判斷語言：回傳 LANGS 裡的 family（zh / ko / ja / th / ru / en / es …）；None 表示沒有可翻譯的文字。"""
    hangul = kana = han = latin = thai = cyrillic = 0
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            hangul += 1
        elif 0x3040 <= o <= 0x30FF:
            kana += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            han += 1
        elif 0x0E00 <= o <= 0x0E7F:
            thai += 1
        elif 0x0400 <= o <= 0x04FF:
            cyrillic += 1
        elif ch.isalpha() and (o < 0x250 or 0x1E00 <= o <= 0x1EFF):
            latin += 1
    if hangul and hangul >= kana:
        return "ko"
    if kana:
        return "ja"
    if han:
        return "zh"
    if thai >= 2:
        return "th"
    if cyrillic >= 2:
        return "ru"
    if latin >= 2:
        return latin_language(text)
    return None


def apply_glossary(text: str, glossary: dict[str, str], target: str | None = None) -> tuple[str, list[str]]:
    """
    把原文裡的專有名詞直接換成指定的譯文（一次掃完，不會重複替換）。
    譯文是英文字母的（例如 Chovy）兩個方向都適用；譯文是韓文或中文的，只在翻成那個語言時才用，
    所以可以同時寫「초비 = Chovy」和「Chovy = 쵸비」。
    """
    if target:
        family = LANGS[target]["family"]
        glossary = {k: v for k, v in glossary.items() if detect_language(v) in (None, family) or detect_language(v) in LATIN}
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


# 還沒說完的半句：實測沒有這一段的話，模型會把「이제 과학적」翻成一整句「現在要用科學的方式。」；有了就只翻聽到的
# 「保留語氣」：髒話、玩笑、網路用語照原本的強度翻成對方語言的口語（預設的直翻會被模型收斂成比較客氣的說法）。
# 實測沒有給例子時，翻成台灣中文會出現大陸用語（卧槽），所以每個語言給幾個當地常用的說法。
# 不給整句示範：試過，4B 會把示範的譯文原封不動抄出來當成答案
KEEP_TONE = ("Keep the speaker's attitude and strength: translate swear words, insults, teasing, jokes and internet slang "
             "into equally strong, natural {lang} slang{examples}. Do not soften, censor or explain them, "
             "and do not add any that are not in the original.\n")
KEEP_EXAMPLES = {"zh-TW": " as used in Taiwan (for example 幹, 靠北, 笑死, 很瞎, 雷)", "zh-CN": " (for example 卧槽, 笑死, 太菜了, 坑)",
                 "ko": " (for example 씨발, 존나, ㅋㅋ, 개-)", "ja": " (for example クソ, マジ, 草, ヤバい)",
                 "en": " (for example damn, wtf, lmao, trash)", "vi": " (for example vãi, đm, gà)",
                 "th": " (for example เชี่ย, 555, กาก)", "id": " (for example anjir, wkwk, noob)",
                 "es": " (for example joder, jaja, manco)", "pt": " (for example porra, kkkk, ruim demais)",
                 "ru": " (for example блин, лол, рак)", "de": " (for example scheiße, lol, Noob)",
                 "fr": " (for example putain, mdr, nul)"}

def keep_tone_text(target: str) -> str:
    return KEEP_TONE.format(lang=LANGS[target]["en"], examples=KEEP_EXAMPLES.get(target, ""))


UNFINISHED = ("The speaker is still talking, so the text may stop in the middle of a sentence. "
              "Translate only the words that are there. Do not finish or guess the rest of the sentence.\n")


def build_prompt(text: str, source: str, target: str, glossary: dict[str, str], tone: str = "casual",
                 hints: list[tuple[str, str]] | None = None, examples: list[tuple[str, str]] | None = None,
                 style: str = "full", context: list[str] | None = None, hint_intro: str | None = None,
                 unfinished: bool = False, keep: bool = False) -> str:
    """TranslateGemma 官方的提示格式，加上聊天語氣提示、專有名詞表和遊戲用語提示（實測過效果最好的組合）。
    unfinished：這是還沒說完的半句（即時字幕）。跟模型說清楚，不然它會自己把句子編完。
    style：full = 平常用的；short = 語氣提示換成比較短的寫法（直述句被翻成問句時重翻用）；
    plain = 只有官方的提示（模型把原文原封不動吐回來時重翻用，意思最穩但語氣會比較正式）。
    keep：保留語氣（髒話、玩笑、網路用語照原本的強度翻），plain 以外的寫法都加。"""
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
    if keep and style != "plain":
        extra += keep_tone_text(target)
    if kept:
        extra += "Keep these terms exactly as written, do not translate or change them: " + "; ".join(kept) + "\n"
    hints = [(a, b) for a, b in (hints or []) if a in text]
    if hints and style != "plain":
        extra += (hint_intro or HINT_INTRO) + "; ".join(f"{a} = {b}" for a, b in hints) + "\n"
    words = [(a, b) for a, b in COMMON_WORDS.get((source, target), [])
             if a in text and not text.rstrip("。．.!！~～… ").endswith(a)]      # 單獨一句「等一下」是「等等我」，不算
    if source == "zh":
        words += slang_in(text, target)
    if words and style != "plain":
        extra += "Word meanings: " + "; ".join(f"{a} = {b}" for a, b in words) + "\n"
    places = places_in(text) if source == "zh" else []
    if places and style != "plain":
        extra += PLACES_INTRO + "; ".join(f"{a} = {b}" for a, b in places) + "\n"
    if examples and style != "plain":
        extra += EXAMPLES_INTRO + "".join(f"{a} => {b}\n" for a, b in examples)
    if context and style == "full":
        extra += CONTEXT_INTRO + "".join(f"- {line}\n" for line in context) + "\n"
    if unfinished:
        extra += UNFINISHED
    body = (
        f"You are a professional {s_name} ({s_code}) to {t_name} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {s_name} text "
        f"while adhering to {t_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"{extra}"
        f"Produce only the {t_name} translation, without any additional explanations or commentary. "
        f"Please translate the following {s_name} text into {t_name}:\n\n\n{text}"
    )
    return f"<start_of_turn>user\n{body}<end_of_turn>\n<start_of_turn>model\n"


def _script(family: str) -> str:
    """這個語言用哪一種文字寫：hangul / cjk（漢字、假名）/ thai / cyrillic / latin。"""
    return {"ko": "hangul", "zh": "cjk", "ja": "cjk", "th": "thai", "ru": "cyrillic"}.get(family, "latin")


def _in_script(ch: str, script: str) -> bool:
    o = ord(ch)
    if script == "hangul":
        return 0xAC00 <= o <= 0xD7A3
    if script == "cjk":
        return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3040 <= o <= 0x30FF
    if script == "thai":
        return 0x0E00 <= o <= 0x0E7F
    if script == "cyrillic":
        return 0x0400 <= o <= 0x04FF
    return False      # 拉丁字母：專有名詞、遊戲用語本來就會留著，不能拿來判斷有沒有翻


def _leftover(out: str, source: str, target: str) -> int:
    """譯文裡還留著幾個原文的字（韓文翻成別的語言卻還有韓文字，之類的）。"""
    have, want = _script(source), _script(LANGS[target]["family"])
    if have == want:
        return 0
    return sum(1 for ch in out if _in_script(ch, have))


def _is_source_char(ch: str, source: str) -> bool:
    return _in_script(ch, _script(source))


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


_KO_LAUGH = [(re.compile(r"\s*[ㅋㅎ]{2,}"), {"zh-TW": " 哈哈", "zh-CN": " 哈哈", "en": " haha", "ja": " www", "vi": " haha", "th": " 555",
                                            "id": " wkwk", "es": " jaja", "pt": " kkkk", "ru": " ахах", "de": " haha", "fr": " mdr"}),
             (re.compile(r"\s*[ㅠㅜ]{2,}"), {"zh-TW": " 嗚嗚", "zh-CN": " 呜呜", "en": " T_T", "ja": " 泣", "vi": " T_T", "th": " T_T",
                                            "id": " T_T", "es": " T_T", "pt": " T_T", "ru": " T_T", "de": " T_T", "fr": " T_T"})]


def tidy(out: str, target: str, keep=()) -> str:
    """模型有時把韓文的「ㅋㅋ」「ㅠㅠ」原樣留在譯文裡，換成看得懂的寫法。
    keep：使用者設成「不翻的字」的（2.14.0）。裡面有 ㅋㅋ 之類的，就照原樣留著不換。"""
    if LANGS[target]["family"] != "ko":
        for pattern, words in _KO_LAUGH:
            if any(pattern.fullmatch(str(k)) for k in keep):
                continue
            out = pattern.sub(words.get(target, ""), out)
    return out.strip()


# ---- 超譯檢查：模型多寫了原文沒有的東西

# 模型沒在翻譯、而是跟使用者說話（螢幕上讀到幾個亂碼字時最常見）：「好的，請提供您要翻譯的韓文文字。」
# 「這句話無法理解，無法翻譯。」「Please provide the text」。這些不是譯文，不能給人看
_META = re.compile(
    r"請提供|请提供|無法(理解|翻譯|辨識)|无法(理解|翻译|识别)|要翻譯的|要翻译的|翻譯的(文字|內容|句子)|翻译的(文字|内容|句子)|"
    r"原文|上下文|"
    r"please provide|provide the text|cannot (be )?translate|unable to translate|can't translate|the text you provided|"
    r"번역할|번역해 ?드|제공해 ?주|원문|"
    r"翻訳する|翻訳して|提供してください|原文",
    re.IGNORECASE)


def meta_reply(out: str, text: str) -> bool:
    """譯文其實是模型在跟人說話（要人提供文字、說翻不了），不是翻譯。原文自己有這些字的話不算。"""
    found = _META.search(out)
    return bool(found) and found.group(0).lower() not in text.lower()


_NOTE = re.compile(r"\s*[（(][^（()）]*[)）]")


def drop_notes(out: str, text: str) -> str:
    """原文沒有括號、譯文卻有：那是模型自己加的註解（「（然後省略了後面的內容）」「(Hòu miàn de!)」），拿掉。"""
    if re.search(r"[（(]", text):
        return out
    cut = _NOTE.sub("", out).strip()
    return cut if len(cut) >= 1 else out


def extra_lines(out: str, text: str, source: str, target: str) -> str:
    """原文只有一行、譯文卻有好幾行：第一行是模型的答案，後面是它自己接下去編的
    （「Savi」→「Savi:\n你現在在幹嘛？」、「보이오이」→「好的。\n裝飾。\n好的。」），只留第一行。
    第一行還留著原文的字（模型先抄一遍原文、下一行才是譯文）就不動，交給 salvage。"""
    if "\n" in text.strip():
        return out
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    if len(lines) <= 1 or _leftover(lines[0], source, target):
        return out
    first = lines[0]
    return first[:-1].rstrip() if first.endswith((":", "：")) and not text.rstrip().endswith((":", "：")) else first


def trim_extra(out: str, text: str, source: str, target: str) -> str:
    """把模型多寫的東西拿掉：自己接下去編的行、自己加的括號註解。"""
    return drop_notes(extra_lines(out, text, source, target), text)


def over_translated(text: str, out: str, source: str, target: str) -> bool:
    """以前記下來的機器翻譯有沒有超譯的樣子（多出來的行、自己加的註解、跟人說話、長得不合理）：有就不用，重翻。"""
    return trim_extra(out, text, source, target) != out.strip() or meta_reply(out, text) or too_long(text, out, source, target)


# 一個原文單位翻成目標語言大約幾個單位（中日韓泰：一個字；其他：一個詞）。用使用者的翻譯紀錄量的中位數
_RATE = {("cjk", "hangul"): 1.5, ("hangul", "cjk"): 1.0, ("latin", "cjk"): 1.6, ("cjk", "latin"): 0.7,
         ("hangul", "latin"): 0.6, ("latin", "hangul"): 1.8}


def _units(text: str, script: str) -> int:
    """長度：中日韓泰的字一個算一個，拉丁字母和數字一個詞算一個（名字、遊戲用語在哪一種語言都是一個詞）。"""
    words = len(re.findall(r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF]+", text))
    if script in ("cjk", "hangul", "thai"):
        return sum(1 for ch in text if _in_script(ch, "cjk") or _in_script(ch, "hangul") or _in_script(ch, "thai")) + words
    return words


def too_long(text: str, out: str, source: str, target: str) -> bool:
    """譯文長得不合理（比平常的翻譯長四成以上，再加兩個字的空間）：多半是多加了一句原文沒有的話。"""
    have, want = _script(source), _script(LANGS[target]["family"])
    rate = _RATE.get((have, want), 1.0 if have == want else 1.3)
    n = _units(text, have)
    return n >= 3 and _units(out, want) > rate * n * 1.4 + 2


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


# 其他語言常見的小數字寫法（譯文寫成文字也算有翻到）：0 到 10、20、30（半）、100、1000
_WORDS_RAW = {
    "es": "cero,nada|uno,una,un,primer,primero|dos,segundo,ambos|tres,tercer|cuatro|cinco|seis|siete|ocho|nueve|diez|veinte|"
          "treinta,media|cien,ciento|mil",
    "pt": "zero,nada|um,uma,primeiro|dois,duas,segundo,ambos|três,tres|quatro|cinco|seis|sete|oito|nove|dez|vinte|trinta,meia|"
          "cem,cento|mil",
    "fr": "zéro,zero,aucun|un,une,premier,première|deux,second,deuxième|trois|quatre|cinq|six|sept|huit|neuf|dix|vingt|"
          "trente,demi,demie|cent|mille",
    "de": "null,kein,keine|eins,ein,eine,einen,erste,ersten|zwei,zweite,beide|drei,dritte|vier|fünf|sechs|sieben|acht|neun|"
          "zehn|zwanzig|dreißig,halbe,halb|hundert|tausend",
    "ru": "ноль,нет|один,одна,одно,первый|два,две,второй,оба|три,третий|четыре|пять|шесть|семь|восемь|девять|десять|"
          "двадцать|тридцать,полчаса,пол|сто|тысяча,тысячу",
    "id": "nol,tidak|satu,se,pertama|dua,kedua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|dua puluh|"
          "tiga puluh,setengah|seratus|seribu",
    "vi": "không|một,mot,nhất|hai|ba|bốn,bon,tư|năm,nam|sáu,sau|bảy,bay|tám,tam|chín,chin|mười,muoi|hai mươi|"
          "ba mươi,rưỡi,nửa|trăm|nghìn,ngàn",
    "th": "ศูนย์,ไม่มี|หนึ่ง,แรก|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ยี่สิบ|สามสิบ,ครึ่ง|ร้อย|พัน",
}
_WORDS = {lang: dict(zip((0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30, 100, 1000), (tuple(p.split(",")) for p in raw.split("|"))))
          for lang, raw in _WORDS_RAW.items()}


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
    elif family in _WORDS:
        out += _WORDS[family].get(n, ())
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


def repair_numbers(original: str, out: str, target: str, source: str | None = None) -> str:
    """數字保護（2.14.0）：換寫法重翻之後數字還是對不上時，最後一步直接把數字改回原文的。
    只在「原文和譯文用阿拉伯數字寫的數一樣多」時才換，一個對一個照順序換（「3點」被翻成「30點」→ 改回 3）；
    數量對不上（譯文把數字寫成字、或多了少了）就不動，留給畫面提醒使用者核對。改不了就回傳原本的譯文。"""
    if not numbers_lost(original, out, target, source):
        return out
    want = numbers(original, source)
    found = list(_DIGITS.finditer(unicodedata.normalize("NFKC", out)))
    if not want or len(found) != len(want) or unicodedata.normalize("NFKC", out) != out:
        return out
    pieces, last = [], 0
    for m, n in zip(found, want):
        pieces.append(out[last:m.start()])
        pieces.append(n)
        last = m.end()
    pieces.append(out[last:])
    fixed = "".join(pieces)
    return fixed if not numbers_lost(original, fixed, target, source) else out


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


class Stopped(Exception):
    """翻到一半被叫停（on_part 回傳 False，或有人按了 Stop）。不是錯誤。"""


class Stop:
    """交給 translate(stop=…) 的「停」按鈕：呼叫 stop() 就馬上中斷那一次翻譯（丟出 Stopped）。
    引擎還在讀原文、一個字都還沒吐出來的時候也停得下來（即時字幕：整句來了，半句就不用翻了）。
    一個 Stop 只管一次翻譯，不會誤停到別的句子。"""

    def __init__(self):
        self.asked = False

    def stop(self) -> None:
        self.asked = True


class Translator:
    turn = None            # 見 __init__
    def __init__(self, url: str, glossary=None, tone: str = "casual", max_chars: int = 500, hints=None, lessons=None,
                 memory=None):
        """glossary：呼叫後回傳 {原文: 譯文}；hints：hints(原文, 來源語言, 目標語言) 回傳 [(詞, 建議譯法)]；
        lessons：呼叫後回傳使用者教過的句子 [{"src", "dst", "lang", "target"}]；
        memory：翻過的句子（livelingo.memory.Memory），翻過的直接用、翻好的記下來；None = 不用。"""
        self.url = url.rstrip("/")
        self.key = ""                     # 翻譯引擎這一次啟動的金鑰（Engine.key）；空的就不帶
        self.glossary = glossary or (lambda: {})
        self.keep = lambda: []            # 不翻的字（2.14.0）：照原樣留著，ㅋㅋ 也不換成「哈哈」
        self.hints = hints or (lambda text, source, target: [])
        self.lessons = lessons or (lambda: [])
        self.memory = memory
        self.model = ""                   # 現在用哪個模型（"4b"／"12b"）：記住的翻法比現在的模型差就重翻
        self.tone = tone
        self.keep_tone = False            # 保留語氣（髒話、玩笑、網路用語照原本的強度翻）；記住的翻法跟直翻的分開
        self.hint_intro = HINT_INTRO      # 用語提示前面那句話（照選了哪些用語包換）
        self.max_chars = max_chars
        self._lock = threading.Lock()  # 一次只翻一則，其他排隊
        self._cache: dict[tuple, str] = {}
        self._came = threading.local()    # 這條執行緒上一次翻譯用了記住的翻法的話，是哪來的
        # turn()：回傳「輪到我用處理器」的鎖（翻譯引擎和語音辨識都用處理器的電腦上，兩個輪流跑，見 Service._cpu_turn）。
        # None = 不用輪流
        self.turn = None

    def came_from(self) -> dict | None:
        """上一次 translate() 用的是記住的翻法：{"from": "mine" | "shared" | "subtitle", "near": 是不是同一部裡幾乎一樣的句子}；
        是模型翻的（或沒用記憶）就 None。"""
        return getattr(self._came, "value", None)

    def _my_turn(self):
        import contextlib

        return self.turn() if self.turn else contextlib.nullcontext()

    def warm(self, pairs) -> int:
        """引擎剛開好時先「暖機」：把每個翻譯方向固定不變的那段提示先算好放進引擎的快取。
        實測（處理器、4B）：沒暖機時每個方向的第一句要 3.4 到 4 秒，暖機後 1 到 1.7 秒（之後的句子本來就是這麼快）。
        pairs：[(來源語言 family, 目標語言代碼)]。只叫引擎讀提示、吐 1 個字，不會記進任何快取或翻過的句子。回傳暖了幾個方向。"""
        done = 0
        for source, target in pairs:
            if source not in SOURCES or target not in LANGS or source == LANGS[target]["family"]:
                continue
            with self._lock, self._my_turn():
                try:
                    self._ask("ok", source, target, [], max_tokens=1)
                    done += 1
                except Exception:  # noqa: BLE001  暖不起來沒關係，第一句慢一點而已
                    return done
        return done

    def memory_tone(self) -> str:
        """記住的翻法用哪個語氣分開存：保留語氣翻出來的跟直翻的不混用。"""
        return self.tone + ("+keep" if self.keep_tone else "")

    def second_opinion(self, text: str, target: str, source: str | None = None) -> str:
        """換一種寫法（簡短的）再翻一次（2.15.0：沒把握的句子拿來跟第一次比）。不用暫存、不記，翻不出來回傳空字串。"""
        text = text.strip()[:MAX_TEXT]
        source = source or detect_language(text) or "en"
        if not text or source == LANGS[target]["family"]:
            return ""
        kind = sentence_kind(text, source)
        sent = with_end_mark(plain_zh(text) if source == "zh" else text, source, kind)
        hints = self.hints(text, source, target)
        with self._lock, self._my_turn():
            out = trim_extra(self._ask(sent, source, target, hints, [], "short"), sent, source, target)
        if meta_reply(out, sent):
            return ""
        return tidy(to_script(out, target), target, self.keep())

    def clear_cache(self) -> None:
        self._cache.clear()

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _ask(self, text: str, source: str, target: str, hints: list, examples: list | None = None,
             style: str = "full", context: list[str] | None = None, on_part=None, unfinished: bool = False,
             stop: Stop | None = None, max_tokens: int | None = None) -> str:
        """on_part(目前翻出來的字)：有給的話，模型每吐出幾個字就叫一次（畫面上譯文一個字一個字出來，不用等整句）。
        on_part 回傳 False 表示「不用翻完了」：馬上停，丟出 Stopped。stop：見 Stop。"""
        payload = {
            "prompt": build_prompt(text, source, target, self.glossary(), self.tone, hints, examples, style, context,
                                   self.hint_intro, unfinished, self.keep_tone),
            "max_tokens": max_tokens or min(1024, 64 + 4 * len(text)),
            "temperature": 0,
            "repeat_penalty": 1.0,
            "stop": ["<end_of_turn>"],
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            f"{self.url.rstrip('/')}/v1/completions", data=json.dumps(payload).encode("utf-8"), headers=headers)
        if on_part is None and stop is None:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["text"].strip()
        payload["stream"] = True
        out = ""
        stream = self._stream(json.dumps(payload).encode("utf-8"), headers, stop)
        try:
            for raw in stream:      # 一行一筆：「data: {…}」，最後一筆是「data: [DONE]」
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                piece = (json.loads(line[5:])["choices"][0].get("text") or "")
                if piece:
                    out += piece
                    try:
                        keep = on_part(out) if on_part else None
                    except Exception:  # noqa: BLE001  畫面那邊出錯不能讓翻譯失敗
                        keep = None
                    if keep is False:      # 不用翻完了（這句話已經有更新的內容）
                        raise Stopped()
        finally:
            stream.close()      # 連線一關，引擎就會停下來
        if stop is not None and stop.asked:
            raise Stopped()
        return out.strip()

    def ask_free(self, body: str, max_tokens: int = 300, stop: Stop | None = None) -> str:
        """不是翻譯的請求（回話建議、對話摘要、生字解釋，core/assist.py）：用同一個模型、同一條排隊，
        一次只做一件事，不會跟翻譯搶。body 是使用者那一段的內容（格式由這裡包好）。"""
        payload = {"prompt": f"<start_of_turn>user\n{body}<end_of_turn>\n<start_of_turn>model\n",
                   "max_tokens": int(max_tokens), "temperature": 0, "repeat_penalty": 1.0, "stop": ["<end_of_turn>"]}
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        with self._lock, self._my_turn():      # 處理器上也跟語音辨識輪流（見 Service._cpu_turn）
            if stop is not None and stop.asked:
                raise Stopped()
            req = urllib.request.Request(f"{self.url.rstrip('/')}/v1/completions", data=json.dumps(payload).encode("utf-8"),
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["text"].strip()

    def _stream(self, body: bytes, headers: dict, stop: Stop | None = None):
        """跟引擎要「一邊翻一邊回」，一行一行交出來。
        自己開連線、自己讀：等引擎的時候每 0.1 秒看一次有沒有人按了停（用 urlopen 的話會一直卡到引擎回第一個字，
        在 Windows 上從別的地方也叫不醒它）。連線一關，引擎就會停下那一次的工作。"""
        import select
        import socket
        import urllib.error
        import urllib.parse

        url = urllib.parse.urlsplit(f"{self.url.rstrip('/')}/v1/completions")
        port = url.port or 80
        sock = socket.create_connection((url.hostname, port), timeout=30)

        def more() -> bytes:
            waited = 0.0
            while True:
                if stop is not None and stop.asked:
                    raise Stopped()
                if select.select([sock], [], [], 0.1)[0]:
                    return sock.recv(65536)
                waited += 0.1
                if waited >= 180:
                    raise TimeoutError("timed out")

        try:
            head = [f"POST {url.path} HTTP/1.1", f"Host: {url.hostname}:{port}", "Connection: close", "Accept: text/event-stream",
                    f"Content-Length: {len(body)}"] + [f"{k}: {v}" for k, v in headers.items()]
            sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)
            buf = b""
            while b"\r\n\r\n" not in buf:
                data = more()
                if not data:
                    raise ConnectionError("the engine closed the connection")
                buf += data
            head_bytes, buf = buf.split(b"\r\n\r\n", 1)
            lines = head_bytes.decode("latin-1").split("\r\n")
            status = int(lines[0].split()[1])
            if status != 200:
                raise urllib.error.HTTPError(self.url, status, lines[0], None, None)
            chunked = any(line.lower().replace(" ", "") == "transfer-encoding:chunked" for line in lines[1:])
            text, done = b"", False
            while True:
                if chunked:      # 一塊一塊來的：「長度（十六進位）\r\n 內容 \r\n」，長度 0 是結束
                    while True:
                        cut = buf.find(b"\r\n")
                        if cut < 0:
                            break
                        size = int(buf[:cut].split(b";")[0] or b"0", 16)
                        if size == 0:
                            done = True
                            break
                        if len(buf) < cut + 2 + size + 2:
                            break
                        text += buf[cut + 2:cut + 2 + size]
                        buf = buf[cut + 2 + size + 2:]
                else:
                    text, buf = text + buf, b""
                while b"\n" in text:
                    line, text = text.split(b"\n", 1)
                    yield line
                if done:
                    break
                data = more()
                if not data:
                    break
                buf += data
            if text:
                yield text
        finally:
            sock.close()

    def busy(self) -> bool:
        """現在是不是正在翻。"""
        return self._lock.locked()

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
            if meta_reply(other, text):      # 跟人說話的那種不算譯文，留著原文的字都比它好
                continue
            if _leftover(other, source, target) < _leftover(best, source, target):
                best = other
            if not _leftover(best, source, target):
                break
        return best

    def translate(self, text: str, target: str, source: str | None = None, context: list[str] | None = None,
                  on_part=None, unfinished: bool = False, stop: Stop | None = None, remember: str | None = None,
                  work: str = "", fresh: bool = False) -> str:
        """target：zh-TW / zh-CN / ko / en / ja。原文已經是那個語言時不用翻，中文只做繁簡轉換。
        context：前面幾句對話的原文（最多用最後兩句），只給模型參考，不會被翻出來。
        on_part(目前的譯文)：翻的時候一邊出字一邊叫（只是先給人看；最後回傳的才是整理過、核對過的譯文）。
        on_part 回傳 False 就停下來不翻了，丟出 Stopped（對方還在說、這半句已經過時的時候用）。
        unfinished：還沒說完的半句（即時字幕）。只翻一次、不補句尾、不核對；結果另外記，不會被當成整句的譯文。
        stop：一個 Stop；別的地方呼叫它的 stop() 就馬上中斷這一次（丟出 Stopped）。
        remember：這句話從哪來的（訊息的 via）；有給的話，翻好的會記進翻過的句子（memory），下次同一句不用重翻。
        work：在看哪一部（works.work_key）；同一部翻過的先用，也記下這句是在這部看到的。
        用了記住的翻法時，came_from() 會說是哪來的（同一條執行緒裡、下一次翻譯之前有效）。
        fresh：一定叫模型重翻（不用暫存、翻過的句子、教過的句子），翻好的也不記。翻譯自我檢查用（qualitycheck.py）。"""
        self._came.value = None
        text = text.strip()[:MAX_TEXT]
        source = source or detect_language(text) or "en"
        if source == LANGS[target]["family"]:
            return to_script(text, target)
        if len(text) > self.max_chars:
            # 太長：以前是悄悄只翻前 500 個字，後面的不見了也沒人知道。現在切成幾段分開翻，再接起來
            parts, joiner, outs = split_long(text, self.max_chars), "" if LANGS[target]["family"] in NO_SPACES else " ", []
            for i, part in enumerate(parts):
                done = joiner.join(outs)
                outs.append(self.translate(part, target, source, context if i == 0 else None, unfinished=unfinished, stop=stop,
                                       remember=remember, work=work,
                                           on_part=(lambda t, done=done: on_part((done + joiner if done else "") + t)) if on_part else None))
            return joiner.join(o for o in outs if o)
        context = [c.strip()[:120] for c in (context or []) if c and c.strip()][-2:]
        key = (text, target, *context, *(["…"] if unfinished else []))
        if key in self._cache and not fresh:
            return self._cache[key]
        exact, examples = self.taught(text, source, target) if not fresh else (None, [])
        if exact:
            return exact
        hints = self.hints(text, source, target)
        terms = ""
        if self.memory is not None and not unfinished and not fresh:
            from ..memory import terms_print

            words = self.glossary()
            terms = terms_print([(k, v) for k, v in words.items() if k and k in text] + [tuple(h) for h in hints])
            found = self.memory.find(text, source, target, self.memory_tone(), self.model, terms, work,
                                     ok=lambda dst: not over_translated(text, dst, source, target))
            if found:
                self._came.value = {"from": found[1], "near": found[2]}
                return found[0]      # 不放進暫存：暫存不知道是哪來的，下次也要能說出來
        kind = sentence_kind(text, source)
        sent = plain_zh(text) if source == "zh" else text
        sent = sent if unfinished else with_end_mark(sent, source, kind)
        keep = self.keep()
        show = (lambda t: on_part(tidy(to_script(t, target), target, keep))) if on_part else None
        with self._lock, self._my_turn():
            if stop is not None and stop.asked:      # 排隊等引擎的時候就被叫停了
                raise Stopped()
            out = self._ask(sent, source, target, hints, examples, context=context, **({"on_part": show} if show else {}),
                            **({"unfinished": True} if unfinished else {}), **({"stop": stop} if stop else {}))
            if unfinished:
                out = tidy(to_script(trim_extra(out, sent, source, target), target), target, keep)
                self._cache[key] = out
                return out
            out = trim_extra(out, sent, source, target)
            if meta_reply(out, sent):
                # 模型沒翻，在跟人說話（「請提供要翻譯的文字」）：只用官方提示再翻一次，還是這樣就不翻，原文照舊
                again = trim_extra(self._ask(sent, source, target, [], [], "plain"), sent, source, target)
                if meta_reply(again, sent):
                    self._cache[key] = text
                    return text
                out = again
            if too_long(sent, out, source, target):
                # 超譯：譯文比平常長很多，多半是把前面幾句也翻進來，或自己多加了一句原文沒有的話。
                # 先不給前文再翻；還是太長就換短的寫法。用第一個長度正常的，都不正常就用最短的
                tries = [out]
                for style in (["full"] if context else []) + ["short"]:
                    again = trim_extra(self._ask(sent, source, target, hints, examples, style), sent, source, target)
                    if meta_reply(again, sent) or _leftover(again, source, target) > _leftover(out, source, target):
                        continue
                    tries.append(again)
                    if not too_long(sent, again, source, target):
                        break
                out = next((t for t in tries if not too_long(sent, t, source, target)), min(tries, key=len))
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
            out = repair_numbers(sent, out, target, source)      # 還是對不上：數字一樣多就直接改回原文的
            if meta_reply(out, sent):      # 重翻（問句、數字、留著原文）的那幾次又變成跟人說話：不翻
                self._cache[key] = text
                return text
        out = to_script(out, target)
        out = tidy(out, target, keep)
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = out
        if (remember is not None and self.memory is not None and out and out.strip() != text
                and not numbers_lost(sent, out, target, source) and not _leftover(out, source, target)):
            # 只記沒問題的：數字對得上、沒有留著原文的字。有問題的下次再翻一次
            self.memory.store(text, source, target, self.memory_tone(), out, self.model, terms, remember, work)
        return out
