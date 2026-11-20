"""
內建的遊戲用語表（英雄聯盟）。翻譯模型常把遊戲裡的說法照字面翻錯，
例如把「탑」翻成「塔」而不是「上路」，所以遇到這些詞時，把建議的譯法一起告訴模型。
只是提示，不是硬換字：模型看上下文覺得不是遊戲用語時，還是可以照一般意思翻。
"""

import json
import re
from pathlib import Path

# 韓文的詞後面常接助詞；只有一個字的用語（탑、용、궁…）要整個詞剛好是它（加助詞）才算
_PARTICLES = {"", "은", "는", "이", "가", "을", "를", "에", "도", "의", "로", "으로", "에서", "만", "랑", "이랑",
              "하고", "까지", "부터", "한테", "이야", "야", "임", "인데", "이나", "나", "요", "이요", "좀"}
_KO_TOKEN = re.compile(r"[가-힣]+")


class TermPack:
    def __init__(self, path: Path | None = None):
        path = path or Path(__file__).resolve().parent / "data" / "terms_lol.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        self.ko_zh = sorted((tuple(p) for p in raw.get("ko_zh", [])), key=lambda p: len(p[0]), reverse=True)
        self.zh_ko = sorted((tuple(p) for p in raw.get("zh_ko", [])), key=lambda p: len(p[0]), reverse=True)

    def find(self, text: str, source: str, target: str) -> list[tuple[str, str]]:
        """回傳這句話裡出現的用語和建議譯法（最多 6 個）。只支援韓文和中文（繁體、簡體）之間。"""
        found: list[tuple[str, str]] = []
        if source == "ko" and target in ("zh-TW", "zh-CN"):
            for token in _KO_TOKEN.findall(text):
                for term, dst in self.ko_zh:        # 長的先比，「정글러」不會被「정글」搶走
                    if not token.startswith(term):
                        continue
                    rest = token[len(term):]
                    if len(term) >= 2 or rest in _PARTICLES:
                        found.append((term, dst))
                        break
            if target == "zh-CN":      # 用語表是繁體寫的，翻成簡體時把建議的譯法也轉成簡體
                from .core.translate import to_script

                found = [(term, to_script(dst, "zh-CN")) for term, dst in found]
        elif source == "zh" and target == "ko":
            from .core.translate import to_script

            low = text.lower()
            traditional = to_script(low, "zh-TW")      # 用語表是繁體寫的；原文是簡體字時轉成繁體再比
            covered = ""
            for term, dst in self.zh_ko:
                if term in covered:
                    continue
                if term in low:
                    found.append((term, dst))
                    covered += " " + term
                elif term in traditional:
                    simplified = to_script(term, "zh-CN")      # 提示裡的原文要跟句子裡寫的一樣，模型才對得上
                    found.append((simplified if simplified in low else term, dst))
                    covered += " " + term
        return list(dict.fromkeys(found))[:6]
