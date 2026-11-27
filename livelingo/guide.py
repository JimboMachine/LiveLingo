"""
說明：程式裡的說明頁，和一頁可以傳給朋友的快速上手（一個獨立的 HTML 檔）。

內容寫在 data/help.json（每種介面語言、結構一樣），兩邊用同一份，不會各說各的。
句子裡的 {overlay} {mic} {copy} {quick} {reply} {pick} {snap} {phrase} {inplace} 是快速鍵、{ptt} 是按住才說話的鍵、{version} 是版本、{download} 是下載網址；
[[…]] 包起來的是鍵盤上的鍵（畫面上會畫成按鍵的樣子）。
給朋友的那一頁用的是預設快速鍵（朋友是新裝的），只放「開始用、打字、快速鍵、隱私」加上安裝步驟。
"""

import html
import json
import re
from pathlib import Path

from .paths import resource_dir

FILE = "help.json"
DOWNLOAD = "https://github.com/JimboMachine/LiveLingo/releases/latest"
FRIEND_SECTIONS = ("start", "typing", "keys", "privacy")
VALUES = ("overlay", "mic", "copy", "quick", "reply", "pick", "snap", "phrase", "inplace", "private", "ptt", "version", "download")
_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads((resource_dir() / "data" / FILE).read_text(encoding="utf-8"))
    return _cache


def _text(lang: str) -> dict:
    data = load()
    return data.get(lang) or data["en"]


def fill(text: str, values: dict, unset: str) -> str:
    """把 {名稱} 換成真的值。沒設定的快速鍵寫「沒設定」，而且不畫成按鍵。"""
    for name in VALUES:
        value = str(values.get(name) or "")
        if not value:
            text = text.replace("[[{" + name + "}]]", unset)
        text = text.replace("{" + name + "}", value or unset)
    return text


def page(lang: str, values: dict) -> dict:
    """程式裡的說明頁：{"title", "intro", "sections": [{"id", "title", "items": [句子]}]}。"""
    text = _text(lang)
    return {"title": text["title"], "intro": fill(text["intro"], values, text["unset"]),
            "sections": [{"id": s["id"], "title": s["title"], "items": [fill(i, values, text["unset"]) for i in s["items"]]}
                         for s in text["sections"]]}


def _mark() -> str:
    """品牌標誌（從介面用的那一份拿，不另外存一份）。"""
    try:
        source = (resource_dir() / "ui" / "brand.js").read_text(encoding="utf-8")
        return re.search(r"markSmall: '(<svg.*?</svg>)'", source).group(1)
    except Exception:  # noqa: BLE001
        return ""


def _rich(text: str) -> str:
    """跳脫之後，把 [[鍵]] 畫成按鍵、網址變成連結。"""
    out = html.escape(text, quote=False)
    out = re.sub(r"\[\[(.+?)\]\]", r"<kbd>\1</kbd>", out)
    return re.sub(r"(https://[^\s<，。、]+)", r'<a href="\1">\1</a>', out)


STYLE = """
:root { --ground: #F5F6F8; --surface: #FFFFFF; --line: #DDE1E7; --text: #15181C; --dim: #4C5560; --accent: #076F49; --key: #ECEEF2; }
@media (prefers-color-scheme: dark) { :root { --ground: #101215; --surface: #171A1E; --line: #2A2F36; --text: #F2F4F6; --dim: #B4BBC6; --accent: #4ADE9A; --key: #23272D; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--ground); color: var(--text); font: 16px/1.7 "Segoe UI", "Microsoft JhengHei UI", "Microsoft YaHei UI", "Malgun Gothic", "Yu Gothic UI", system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 32px 16px 48px; }
header { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }
header svg { width: 56px; height: 56px; flex: none; }
.name { font-size: 28px; font-weight: 700; letter-spacing: -0.01em; }
.name b { color: var(--accent); }
h1 { font-size: 22px; margin: 16px 0 8px; }
.lead { color: var(--dim); margin: 0 0 20px; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 16px 20px; margin-bottom: 14px; break-inside: avoid; }
h2 { font-size: 17px; margin: 0 0 8px; }
ol, ul { margin: 0; padding-left: 22px; }
li { margin: 6px 0; overflow-wrap: anywhere; }
kbd { display: inline-block; padding: 0 7px; border-radius: 6px; border: 1px solid var(--line); border-bottom-width: 2px; background: var(--key); font: 600 13px/1.7 ui-monospace, Consolas, monospace; white-space: nowrap; }
a { color: var(--accent); }
footer { color: var(--dim); font-size: 13px; margin-top: 20px; }
@media print { body { background: #fff; } main { padding: 0; } section { border-color: #bbb; } }
"""


def friend_html(lang: str, values: dict) -> str:
    """給朋友的那一頁：一個獨立的 HTML 檔（樣式和標誌都在裡面，不連外部的東西，可以直接列印）。"""
    text = _text(lang)
    friend, unset = text["friend"], text["unset"]
    parts = [f"<section><h2>{html.escape(friend['install_title'])}</h2><ol>"
             + "".join(f"<li>{_rich(fill(i, values, unset))}</li>" for i in friend["install"]) + "</ol></section>"]
    for section in text["sections"]:
        if section["id"] in FRIEND_SECTIONS:
            tag = "ol" if section["id"] == "start" else "ul"
            parts.append(f"<section><h2>{html.escape(section['title'])}</h2><{tag}>"
                         + "".join(f"<li>{_rich(fill(i, values, unset))}</li>" for i in section["items"]) + f"</{tag}></section>")
    return (f'<!doctype html>\n<html lang="{html.escape(lang)}">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{html.escape(friend["title"])}</title>\n'
            f"<style>{STYLE}</style>\n</head>\n<body>\n<main>\n<header>{_mark()}<div class=\"name\">Live<b>Lingo</b></div></header>\n"
            f"<h1>{html.escape(friend['title'])}</h1>\n<p class=\"lead\">{_rich(fill(friend['lead'], values, unset))}</p>\n"
            + "\n".join(parts) + f"\n<footer>{_rich(fill(friend['footer'], values, unset))}</footer>\n</main>\n</body>\n</html>\n")


def save_friend(folder: Path, lang: str, values: dict) -> Path:
    """存成 LiveLingo-quick-start-語言.html（同名的直接換成新的）。存不進去會丟出 OSError。"""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"LiveLingo-quick-start-{lang}.html"
    path.write_text(friend_html(lang, values), encoding="utf-8")
    return path
