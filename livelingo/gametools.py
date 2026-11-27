"""遊戲和畫面的小工具（2.16.0）：常見遊戲的聊天框範本、自動找聊天框、聊天頻道的顏色、玩家備註。
全部只看這台電腦上的畫面和設定，不連網路。"""

import json
import re
from pathlib import Path

TEMPLATES = Path(__file__).with_name("data") / "game_templates.json"
MIN_BOX = (60, 30)      # 聊天區最小的寬、高（跟 service 的 _valid_regions 一樣）


def templates() -> dict:
    try:
        games = json.loads(TEMPLATES.read_text(encoding="utf-8")).get("games", {})
    except (OSError, ValueError):
        return {}
    return {exe.lower(): t for exe, t in games.items() if isinstance(t, dict) and len(t.get("box", [])) == 4}


def template_region(exe: str, rect, rid: str, name: str) -> dict | None:
    """照範本在這個遊戲視窗（rect = 左, 上, 寬, 高）裡算出一個聊天區；跟著這個程式的視窗走（anchor）。"""
    t = templates().get((exe or "").lower())
    if not t or not rect or rect[2] <= 0 or rect[3] <= 0:
        return None
    fx, fy, fw, fh = (float(v) for v in t["box"])
    left, top = int(rect[0] + fx * rect[2]), int(rect[1] + fy * rect[3])
    width, height = max(MIN_BOX[0], int(fw * rect[2])), max(MIN_BOX[1], int(fh * rect[3]))
    return {"id": rid, "name": name[:30], "left": left, "top": top, "width": width, "height": height, "on": True,
            "app": exe.lower(), "anchor": [left - int(rect[0]), top - int(rect[1])], **({"story": True} if t.get("story") else {})}


# ---- 23. 自動找聊天框：整個畫面讀一次字，找「左邊對齊、一列一列疊起來」最多列的那一疊

def find_chat_box(lines, width: int, height: int, min_rows: int = 3) -> list[int] | None:
    """lines：core.ocr.Line（x, y, w, h, text）。回傳建議的 [左, 上, 寬, 高]，找不到就是 None。
    聊天框的樣子：好幾列左邊差不多對齊、上下間距差不多、字不大，大多在畫面左半邊或下半部。"""
    rows = sorted((ln for ln in lines if ln.text.strip() and ln.h > 0), key=lambda ln: (ln.y, ln.x))
    best, best_score = None, 0.0
    for i, first in enumerate(rows):
        stack = [first]
        for ln in rows[i + 1:]:
            last = stack[-1]
            h = max(last.h, ln.h, 1)
            if abs(ln.x - first.x) <= 1.5 * h and 0.6 * h <= ln.y - last.y <= 2.6 * h:
                stack.append(ln)
            elif ln.y - last.y > 2.6 * h:
                break
        if len(stack) < min_rows:
            continue
        left = min(ln.x for ln in stack)
        right = max(ln.x + max(ln.w, len(ln.text) * ln.h // 2) for ln in stack)
        top, bottom = stack[0].y, stack[-1].y + stack[-1].h
        h = sum(ln.h for ln in stack) / len(stack)
        score = len(stack) * (1.3 if left < width / 2 else 1.0) * (1.2 if top > height / 3 else 1.0) * (1.0 if h < height / 25 else 0.5)
        if score > best_score:
            pad = int(h)
            best_score = score
            best = [max(0, left - pad), max(0, top - pad), right - left + 2 * pad, bottom - top + 2 * pad]
    if not best:
        return None
    best[2] = min(max(best[2], MIN_BOX[0]), width - best[0])
    best[3] = min(max(best[3], MIN_BOX[1]), height - best[1])
    return best


# ---- 21. 聊天頻道分色：一列字的顏色（core.watcher.line_color 量的）歸成幾種頻道色

def channel_of(color: str) -> str:
    """把字的顏色歸成幾個頻道色（同一個頻道的字每次讀到的顏色會差一點）：白／灰算一般，其他照色相分。"""
    if not re.fullmatch(r"#[0-9A-F]{6}", color or ""):
        return ""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    if hi - lo < 40:
        return ""      # 白、灰、黑：一般聊天，不標
    if r >= g and r >= b:
        return "#F28B82" if g < r * 0.75 else "#FDD663"      # 紅 / 黃橘
    if g >= r and g >= b:
        return "#81C995" if b < g * 0.85 else "#78D9EC"      # 綠 / 青
    return "#C58AF9" if r > g else "#8AB4F8"                  # 紫 / 藍


# ---- 22. 玩家備註：同一個遊戲 ID 加暱稱和備註

MAX_PLAYERS = 200


def clean_players(raw) -> dict:
    """{"ID": "備註"}：ID 最多 30 個字、備註最多 60 個字，最多 MAX_PLAYERS 個。"""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for pid, note in list(raw.items())[:MAX_PLAYERS]:
        pid = " ".join(str(pid).split())[:30]
        note = " ".join(str(note or "").split())[:60]
        if pid and note and pid.lower() not in {k.lower() for k in out}:
            out[pid] = note
    return out


def players_in(text: str, notes: dict) -> list[list[str]]:
    """這句話裡提到的、有備註的玩家：[[ID, 備註]]（照 ID 出現的順序，不分大小寫，最多 3 個）。"""
    found = []
    low = (text or "").lower()
    for pid, note in notes.items():
        at = re.search(r"(?<![\w])" + re.escape(pid.lower()) + r"(?![\w])", low)
        if at:
            found.append((at.start(), pid, note))
    return [[pid, note] for _, pid, note in sorted(found)[:3]]
