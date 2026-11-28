"""敏感資料遮蔽（2.21.0）：密碼、信箱、信用卡號、金鑰、身分證字號這類的東西，不會存進歷史、字幕記錄、單字本的例句和摘要。
畫面上照常顯示（要看要翻都可以），只是不留下來。寧可多遮：密碼那一類有「密碼是」「password:」這種記號就遮後面那一個詞。"""

import re

MASK = "▒▒▒"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"(?<![\d])(?:\d[ -]?){12,18}\d(?![\d])")
_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")
_ID = re.compile(r"(?<![\w])(?:[A-Z][12]\d{8}|\d{6}-[1-4]\d{6}|\d{3}-\d{2}-\d{4})(?![\w])")      # 台灣身分證、韓國住民登錄號碼、美國社會安全碼
_WORDS = ("password", "passwd", "passcode", "pwd", "pw", "密碼", "密码", "口令", "パスワード", "비밀번호", "비번", "mật khẩu", "รหัสผ่าน",
          "kata sandi", "contraseña", "senha", "пароль", "passwort", "mot de passe", "pin")
_SEP = r"(?:\s*(?:\b(?:is|are)\b|=|:|：|是|為|为|は|은|는|이|가)\s*|\s+)"
_PASSWORD = re.compile(r"(?i)(?<![A-Za-z0-9_])(" + "|".join(re.escape(w) for w in sorted(_WORDS, key=len, reverse=True)) + r")(" + _SEP + r"|)([^\s,，。;；!！?？]{3,})")


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if flip:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        flip = not flip
    return total % 10 == 0


def _card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return MASK if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)


def _password(match: re.Match) -> str:
    """密碼詞後面：有「是」「:」「=」「is」就遮後面那個詞；只有空白隔開或直接黏著（密碼123456）時，後面那個詞要有數字才遮（避免 pin the message 被遮）。"""
    word, sep, value = match.group(1), match.group(2), match.group(3)
    explicit = bool(sep.strip())
    if not explicit and not re.search(r"\d", value):
        return match.group(0)
    return word + sep + MASK


def mask(text) -> str:
    """把敏感的部分換成 ▒▒▒，其他不動。"""
    out = str(text or "")
    if not out:
        return out
    out = _KEY.sub(MASK, out)
    out = _EMAIL.sub(MASK, out)
    out = _ID.sub(MASK, out)
    out = _CARD.sub(_card, out)
    return _PASSWORD.sub(_password, out)


def has_secret(text) -> bool:
    return mask(text) != str(text or "")


def mask_row(row: dict, fields=("original", "translated", "place")) -> dict:
    """複製一份，指定的欄位遮好（原本的不動）。"""
    out = dict(row)
    for key in fields:
        if isinstance(out.get(key), str):
            out[key] = mask(out[key])
    return out
