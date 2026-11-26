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
