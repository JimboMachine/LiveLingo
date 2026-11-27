"""快捷鍵一覽小卡（2.17.0）：按一組鍵，浮動字幕上跳出現在所有的快速鍵（鍵盤和手把），不用記。
動作的名字跟設定頁一樣，直接從介面的文字檔（ui/i18n.js 的 hk_ 開頭）拿，不另外翻一份。"""

import json
import re
from functools import lru_cache
from pathlib import Path

I18N = Path(__file__).with_name("ui") / "i18n.js"


@lru_cache(maxsize=16)
def labels(lang: str) -> dict:
    """{動作: 名字}，例如 {"quick": "快速輸入框"}。找不到這個語言就用英文。"""
    try:
        text = I18N.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for code in (lang, "en"):
        start = text.find(f'  "{code}": {{\n')
        if start < 0:
            continue
        end = text.find('\n  },', start)
        block = text[start:end if end > 0 else len(text)]
        for name, raw in re.findall(r'(?<![\w])hk_([a-z]+): ("(?:[^"\\]|\\.)*")', block):
            if name not in out:
                try:
                    out[name] = json.loads(raw)
                except ValueError:
                    continue
    return out


def lines(hotkeys: dict, pads: dict, lang: str) -> list[str]:
    """一行一個：「Ctrl+Shift+Q  快速輸入框」；手把的接在後面，組合用 [ ] 括起來（例如 [Back+A]）。"""
    names = labels(lang)
    out = [f"{combo}  {names.get(action, action)}" for action, combo in hotkeys.items() if combo]
    out += [f"[{combo}]  {names.get(action, action)}" for action, combo in pads.items() if combo]
    return out
