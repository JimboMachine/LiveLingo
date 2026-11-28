"""歷史時間軸（2.20.0）：把歷史照時間切成一段一段（中間安靜太久就換一段），
每段有開始和結束的時間、幾句、誰說了幾句、第一句對方說的話，展開可以看整段。"""

from .recap import sessions

MAX_SESSIONS = 40
MAX_LINES = 60
VIA = ("screen", "heard", "spoken", "typed")


def build(rows: list[dict], gap: float, limit: int = MAX_SESSIONS) -> list[dict]:
    """rows：歷史的句子（有 at、who、via、original、translated）。回傳最新的一段在前面。"""
    out = []
    for lines in sessions(sorted(rows, key=lambda r: float(r.get("at") or 0)), gap):
        lines = [r for r in lines if r.get("original") or r.get("translated")]
        if not lines:
            continue
        first = next((r for r in lines if r.get("who") != "me"), lines[0])
        out.append({
            "id": str(lines[0].get("k") or lines[0].get("at")),
            "start": float(lines[0].get("at") or 0), "end": float(lines[-1].get("at") or 0), "count": len(lines),
            "me": sum(1 for r in lines if r.get("who") == "me"), "them": sum(1 for r in lines if r.get("who") != "me"),
            "via": sorted({r.get("via") for r in lines if r.get("via") in VIA}),
            "preview": str(first.get("translated") or first.get("original") or "")[:80],
            "lines": [{"who": r.get("who", ""), "original": str(r.get("original") or ""), "translated": str(r.get("translated") or ""),
                       "at": float(r.get("at") or 0)} for r in lines[:MAX_LINES]],
        })
    out.reverse()
    return out[:max(1, int(limit))]
