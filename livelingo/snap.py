"""
框選翻譯（2.11.0）：按快速鍵，拉一個框，只讀那一次、只翻那一次。適合任務說明、道具描述、選單這種不會一直變的字。
這裡只做一件事：把文字辨識讀到的一列一列字，接成「一段一段」，一段翻一次（翻整段比翻一行一行準）。
"""

import re

MAX_GROUPS = 8         # 一個框最多翻幾段（再多就是整個畫面，不是一個說明了）
MIN_SCORE = 0.5        # 辨識的把握低於這個的列不要
_NO_SPACE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")      # 日文假名、漢字：詞和詞之間不空格（韓文有空格，所以不算）
_LETTER = re.compile(r"[^\W\d_]")


def _join(a: str, b: str) -> str:
    """接兩列：兩邊都是漢字或日文假名就直接接，不然中間補一個空格（韓文、英文詞之間本來就有空格）。"""
    if a and b and _NO_SPACE.match(a[-1]) and _NO_SPACE.match(b[0]):
        return a + b
    return f"{a} {b}"


def paragraphs(lines: list, min_score: float = MIN_SCORE, limit: int = MAX_GROUPS) -> list[str]:
    """lines：core.ocr.Line 的清單（有 text、x、y、h、score）。上下緊貼、左邊差不多對齊的接成一段；
    同一橫列上離得遠的（選單的兩欄）、中間隔了空白的，各自一段。只有符號、數字的不要。"""
    rows = sorted((l for l in lines if l.score >= min_score and l.text.strip()), key=lambda l: (l.y, l.x))
    groups: list[list] = []
    for row in rows:
        for group in reversed(groups[-4:]):      # 隔壁欄的字夾在中間時，也找得到自己上面那一行
            last = group[-1]
            h = max(last.h, row.h, 1)
            dy = row.y - last.y
            same_row = abs(dy) < 0.5 * h
            below = 0.5 * h <= dy <= 1.9 * h and abs(row.x - last.x) <= 4 * h
            if below or (same_row and 0 <= row.x - (last.x + max(last.w, 0)) <= 1.5 * h):
                group.append(row)
                break
        else:
            groups.append([row])
    out: list[str] = []
    for group in groups:
        text = ""
        for row in group:
            text = _join(text, row.text.strip()) if text else row.text.strip()
        if _LETTER.search(text):
            out.append(text)
    return out[:limit]
