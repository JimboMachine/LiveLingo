"""
匯入字幕檔（.srt / .vtt）當成翻譯：一部影片的原文字幕配上翻譯字幕，一句一句記進翻過的句子（memory.py），
之後看同一部（works.py），聽到、讀到同一句就直接用字幕的翻法，不用叫模型翻。

兩種檔案都收：
- 兩個檔：原文一個、翻譯一個。照時間對：兩句在時間上重疊一半以上才算同一句，對不上的不收。
- 一個雙語檔：每一段裡有原文的行、也有翻譯的行。用文字的語言分出哪幾行是原文、哪幾行是翻譯；
  分不出來（例如兩邊都是拉丁字母）而且剛好兩行時，第一行當原文、第二行當翻譯（介面上可以對調）。

字幕是人翻的，所以比機器翻的優先；但常常是濃縮過的（字幕有字數限制），介面上會提醒先看預覽。
只匯入你有權使用的字幕。全部在這台電腦上處理。
"""

import re

MAX_BYTES = 3_000_000           # 一個字幕檔最大多少（一部電影的字幕大約 100 KB）
MAX_CUES = 20000
MIN_OVERLAP = 0.5               # 兩句在時間上重疊多少（佔比較短那句的比例）才算同一句

_TIME = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_ARROW = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
_TAGS = re.compile(r"<[^>]{0,80}>|\{\\[^}]{0,80}\}")      # <i>、<font …>、<c.yellow>、{\an8}
_SPEAKER = re.compile(r"^\s*[-–]\s+")                     # 「- 對白」開頭的破折號


def _seconds(stamp: str) -> float | None:
    m = _TIME.fullmatch(stamp.strip())
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _clean_line(line: str) -> str:
    line = _TAGS.sub("", line).replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", _SPEAKER.sub("", line)).strip()


def parse(text: str) -> list[dict]:
    """字幕檔的內容 → [{"start", "end", "lines": [每一行]}]，照時間排好。認不得的段落跳過，不會出錯。"""
    text = str(text or "")[:MAX_BYTES].replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        rows = [r for r in block.split("\n") if r.strip()]
        at = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if at is None:
            continue
        m = _ARROW.match(rows[at])
        if not m:
            continue
        start, end = _seconds(m.group(1)), _seconds(m.group(2))
        if start is None or end is None or end < start:
            continue
        lines = [_clean_line(r) for r in rows[at + 1:]]
        lines = [x for x in lines if x and not x.startswith("NOTE")]
        if lines:
            cues.append({"start": start, "end": end, "lines": lines})
        if len(cues) >= MAX_CUES:
            break
    cues.sort(key=lambda c: (c["start"], c["end"]))
    return cues


def language_of(cues: list[dict], detect) -> str | None:
    """整個字幕檔主要是哪一種語言（detect = translate.detect_language）。"""
    counts: dict[str, int] = {}
    for c in cues[:400]:
        lang = detect(" ".join(c["lines"]))
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else None


def pair_two(source: list[dict], target: list[dict]) -> list[tuple[str, str]]:
    """原文字幕和翻譯字幕照時間配對：重疊夠多的才算同一句。一句翻譯只配給一句原文。"""
    out, j, used = [], 0, set()
    for s in source:
        while j < len(target) and target[j]["end"] <= s["start"]:
            j += 1
        best, best_score = None, MIN_OVERLAP
        k = j
        while k < len(target) and target[k]["start"] < s["end"]:
            t = target[k]
            overlap = min(s["end"], t["end"]) - max(s["start"], t["start"])
            shorter = max(0.001, min(s["end"] - s["start"], t["end"] - t["start"]))
            if k not in used and overlap / shorter >= best_score:
                best, best_score = k, overlap / shorter
            k += 1
        if best is not None:
            used.add(best)
            out.append((" ".join(s["lines"]), " ".join(target[best]["lines"])))
    return out


def split_one(cues: list[dict], detect, lang: str | None = None, target_family: str | None = None,
              swap: bool = False) -> tuple[list[tuple[str, str]], str | None, str | None]:
    """一個雙語字幕檔 → [(原文, 翻譯)]、原文語言、翻譯語言。
    lang、target_family：已經知道就照著分；不知道就看每一段裡先出現的語言當原文、另一種當翻譯。
    swap：對調（使用者在預覽裡按「原文和翻譯對調」）。"""
    if lang is None or target_family is None:
        firsts: dict[str, int] = {}
        seconds: dict[str, int] = {}
        for c in cues[:400]:
            langs = [detect(x) for x in c["lines"]]
            langs = [x for x in langs if x]
            if len(set(langs)) >= 2:
                firsts[langs[0]] = firsts.get(langs[0], 0) + 1
                other = next(x for x in langs if x != langs[0])
                seconds[other] = seconds.get(other, 0) + 1
        if firsts and seconds:
            lang = max(firsts, key=firsts.get)
            rest = {k: v for k, v in seconds.items() if k != lang}
            target_family = max(rest, key=rest.get) if rest else None
    if swap:
        lang, target_family = target_family, lang
    out = []
    for c in cues:
        src = [x for x in c["lines"] if lang and detect(x) == lang]
        dst = [x for x in c["lines"] if target_family and detect(x) == target_family]
        if not src or not dst:
            if len(c["lines"]) != 2:
                continue
            src, dst = ([c["lines"][1]], [c["lines"][0]]) if swap else ([c["lines"][0]], [c["lines"][1]])
        out.append((" ".join(src), " ".join(dst)))
    return out, lang, target_family
