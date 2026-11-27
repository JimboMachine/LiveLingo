"""新功能亮點提示（2.20.0）：更新之後，有新功能的設定頁在左邊導覽上有一個「新」的小標記，
點進去看過就消失（一個頁面只標一次）。全新安裝的人一個都不標。"""

from . import changes

# 版本 → 那一版有新功能的設定頁（導覽的 key：history、learn、general…）
HIGHLIGHTS = {
    "2.19.0": ["learn"],
    "2.20.0": ["general", "history"],
}


def all_tabs() -> list[str]:
    return sorted({tab for tabs in HIGHLIGHTS.values() for tab in tabs})


def new_tabs(seen, version: str) -> list[str]:
    """還沒看過的有新功能的頁面（只算這個版本以前的）。"""
    seen = {str(x) for x in (seen if isinstance(seen, list) else [])}
    out: list[str] = []
    try:
        current = changes._key(version)
        for v in sorted(HIGHLIGHTS, key=changes._key):
            if changes._key(v) <= current:
                out += [tab for tab in HIGHLIGHTS[v] if tab not in seen and tab not in out]
    except ValueError:
        return []
    return out
