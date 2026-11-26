"""
請翻譯模型做翻譯以外的小事：回話建議（2.9.0）。全部在這台電腦上跑，用的是同一個翻譯模型，不另外下載。

翻譯模型（TranslateGemma）不是聊天機器人，所以：
- 每件事都寫成固定格式（一行一個，用 ||| 分開），解析不出來的行直接丟掉，寧可少給也不要給怪的；
- 結果一律附「我的語言」的意思，使用者看得懂才決定要不要用。
"""

import re

from .translate import LANGS, REGISTER, to_script

SEP = "|||"
MAX_LINES = 8            # 回話建議最多參考最近幾句
MAX_REPLIES = 3


def _lang_name(code: str) -> str:
    return LANGS[code]["en"] if code in LANGS else code


def _register(target: str, tone: str) -> str:
    """回話要用的說法（韓文半語／해요體……），跟翻譯的語氣設定一樣。"""
    return REGISTER["polite" if tone == "polite" else "casual"].get(target, "")


def conversation(lines: list[dict], my_lang: str) -> str:
    """把最近的對話寫成模型看得懂的樣子：「them: 原文 [譯文]」。lines：[{"who", "original", "translated"}]。"""
    out = []
    for line in lines[-MAX_LINES:]:
        who = "me" if line.get("who") == "me" else "them"
        text = str(line.get("original") or "").strip().replace("\n", " ")[:300]
        tr = str(line.get("translated") or "").strip().replace("\n", " ")[:300]
        if text:
            out.append(f"{who}: {text}" + (f" [{tr}]" if tr and tr != text else ""))
    return "\n".join(out)


def reply_prompt(lines: list[dict], my_lang: str, their_lang: str, tone: str = "casual", n: int = MAX_REPLIES) -> str:
    """回話建議：最近的對話 → n 句用對方語言寫的回覆，每句附我的語言的意思。"""
    mine, theirs = _lang_name(my_lang), _lang_name(their_lang)
    return (f"You are helping a {mine} speaker chat with a friend who speaks {theirs}, during an online game or a voice call. "
            f"Here is the latest part of the conversation (\"me\" is the {mine} speaker; translations are in brackets):\n"
            f"{conversation(lines, my_lang)}\n\n"
            f"Write {n} different short, natural replies that \"me\" could send next, in {theirs}{_register(their_lang, tone)}. "
            f"Make them different from each other (for example agree, ask something back, joke). "
            f"After each reply give its meaning in {mine}.\n"
            f"Output exactly {n} lines and nothing else. Each line must look like: {theirs} reply {SEP} {mine} meaning")


_NUMBER = re.compile(r"^\s*(?:[-*•]|\d+[.)、:：]|\(\d+\))\s*")
_SPEAKER = re.compile(r"^\s*(?:me|them|reply|answer)\s*[:：]\s*", re.I)      # 模型有時照對話的樣子在前面加「me:」
_QUOTES = "\"'“”‘’「」『』"


def _strip(text: str) -> str:
    text = _NUMBER.sub("", text.strip())
    text = _SPEAKER.sub("", text)
    text = text.strip().strip(_QUOTES).strip()
    return text


_ACCENTED = re.compile(r"[\u00C0-\u024F]")      # 帶符號的拉丁字母（é、ě、ñ…）
NOT_LATIN = ("ko", "ja", "zh", "th", "ru")


def parse_replies(out: str, my_lang: str, n: int = MAX_REPLIES, their_lang: str = "") -> list[dict]:
    """模型的回答 → [{"text": 對方語言的回覆, "meaning": 我的語言的意思}]。格式不對的行不要。
    their_lang：回覆應該是哪個語言。不是拉丁字母的語言（韓、日、中、泰、俄），回覆裡混了帶符號的拉丁字母
    （實測 4B 偶爾會在韓文裡冒出一個捷克文的字）就不要那一句。"""
    family = LANGS[their_lang]["family"] if their_lang in LANGS else ""
    found: list[dict] = []
    seen = set()
    for raw in out.splitlines():
        if SEP not in raw:
            continue
        text, _, meaning = raw.partition(SEP)
        text, meaning = _strip(text), _strip(meaning.replace(SEP, " "))
        if not text or not meaning or len(text) > 200 or text.lower() in seen:
            continue
        if family in NOT_LATIN and _ACCENTED.search(text):
            continue
        seen.add(text.lower())
        found.append({"text": text, "meaning": to_script(meaning, my_lang)})
        if len(found) >= n:
            break
    return found


# ---- 對話摘要（2.10.0）

MAX_SUMMARY_LINES = 80     # 一段對話最多拿最後這麼多句來摘要（太長模型會漏，也太慢）
MAX_POINTS = 6
_BULLET = re.compile(r"^\s*(?:[-*•‧·]|\d+[.)、:：]|\(\d+\))\s*")


def summary_lines(lines: list[dict], my_lang: str, names: dict | None = None) -> str:
    """摘要用的對話：都寫成我的語言（對方的話用譯文），這樣模型直接用我的語言整理。
    names：{"me", "them"} 用我的語言寫的稱呼（「我」「對方」）；模型會照著用，比較不會把誰說了什麼弄反。"""
    names = names or {"me": "me", "them": "them"}
    out = []
    for line in lines[-MAX_SUMMARY_LINES:]:
        me = line.get("who") == "me"
        text = str((line.get("original") if me else line.get("translated")) or line.get("original") or "")
        text = text.strip().replace("\n", " ")[:200]
        if text:
            out.append(f"{names['me' if me else 'them']}: {text}")
    return "\n".join(out)


def summary_prompt(lines: list[dict], my_lang: str, names: dict | None = None) -> str:
    mine = _lang_name(my_lang)
    names = names or {"me": "me", "them": "them"}
    return (f"Summarize this game or voice chat in {mine} as 3 to 5 short bullet points: what was decided, plans, "
            f"and anything to remember. Only use what is in the chat; do not add anything. "
            f"Call the two people \"{names['me']}\" and \"{names['them']}\", exactly as in the chat.\n\n"
            f"{summary_lines(lines, my_lang, names)}\n\n"
            f"Output only the bullet points, each on its own line starting with \"- \".")


def parse_summary(out: str, my_lang: str) -> list[str]:
    """模型的摘要 → 一點一點的字串。不是條列的開場白（「以下是摘要：」）丟掉。"""
    points: list[str] = []
    for raw in out.splitlines():
        if not _BULLET.match(raw):
            continue
        text = to_script(_BULLET.sub("", raw).strip().strip(_QUOTES).strip(), my_lang)
        if text and len(text) <= 300 and text not in points:
            points.append(text)
        if len(points) >= MAX_POINTS:
            break
    return points


# ---- 生字的意思（2.10.0）：一般翻譯把單一個字翻得很差（「와드」翻成「哇」），所以帶著整句、說明是遊戲聊天，只要那個字的意思

def gloss_prompt(word: str, sentence: str, my_lang: str) -> str:
    mine = _lang_name(my_lang)
    sentence = str(sentence or "").strip().replace("\n", " ")[:300]
    return (f"Translate only the word \"{word}\" from this sentence into {mine}, with the meaning it has in the sentence "
            f"(this is a chat during an online game or a voice call).\nSentence: {sentence or word}\n"
            f"Answer with the translation only, in a few words.")


def parse_gloss(out: str, word: str, my_lang: str) -> str:
    """模型回答的第一行，去掉引號和句尾的標點。太長（模型整句翻出來）或跟原字一樣就不要。"""
    line = next((x for x in str(out or "").splitlines() if x.strip()), "")
    line = _strip(line).rstrip("。.!！?？,，;；:：")
    line = line.strip().strip(_QUOTES).strip()
    if not line or len(line) > 40 or line.lower() == str(word).lower():
        return ""
    return to_script(line, my_lang)
