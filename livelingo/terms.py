"""
內建的用語包。翻譯模型常把遊戲裡的說法照字面翻錯，
例如把「탑」翻成「塔」而不是「上路」，所以遇到這些詞時，把建議的譯法一起告訴模型。
只是提示，不是硬換字：模型看上下文覺得不是遊戲用語時，還是可以照一般意思翻。
用語包可以多選：lol = 英雄聯盟，chat = 遊戲和語音聊天的縮寫口語（ㄱㄱ、ㅈㅅ…）。
"""

import json
import re
from pathlib import Path

# 韓文的詞後面常接助詞；只有一個字的用語（탑、용、궁…）要整個詞剛好是它（加助詞）才算
_PARTICLES = {"", "은", "는", "이", "가", "을", "를", "에", "도", "의", "로", "으로", "에서", "만", "랑", "이랑",
              "하고", "까지", "부터", "한테", "이야", "야", "임", "인데", "이나", "나", "요", "이요", "좀"}
_KO_TOKEN = re.compile(r"[가-힣]+")
_JAMO_TOKEN = re.compile(r"[ㄱ-ㅎ]+")      # 只有子音的縮寫：ㄱㄱ、ㅈㅅ、ㅊㅋㅊㅋ

PACKS = ("lol", "chat")      # 內建的用語包，檔案是 data/terms_<名字>.json；介面上的名字是 i18n 的 pack_<名字>
# 只跟某一個遊戲有關的用語包 → 那個遊戲的程式檔名（小寫）。開了「照正在玩的遊戲自動切換」時，
# 這些包只有在那個遊戲開著的時候才用；不在這裡的包（chat 聊天縮寫）哪個遊戲都用得到，一直開著
GAME_PACKS = {"lol": ("league of legends.exe",)}
# 認得出來的遊戲：程式檔名（小寫）→ 顯示的名字。只用來在畫面上說「現在在玩什麼」、讓使用者替它加專用詞；
# 不在這裡的程式，使用者也可以從「開著的程式」挑一個當成遊戲
GAMES = {
    "league of legends.exe": "League of Legends",
    "valorant-win64-shipping.exe": "VALORANT",
    "overwatch.exe": "Overwatch 2",
    "tslgame.exe": "PUBG: BATTLEGROUNDS",
    "cs2.exe": "Counter-Strike 2",
    "r5apex.exe": "Apex Legends",
    "r5apex_dx12.exe": "Apex Legends",
    "dota2.exe": "Dota 2",
    "fortniteclient-win64-shipping.exe": "Fortnite",
    "maplestory.exe": "MapleStory",
    "lostark.exe": "Lost Ark",
    "genshinimpact.exe": "Genshin Impact",
    "starrail.exe": "Honkai: Star Rail",
    "eldenring.exe": "ELDEN RING",
    "minecraft.windows.exe": "Minecraft",
    "rocketleague.exe": "Rocket League",
    "fc25.exe": "EA SPORTS FC 25",
    "deltaforce-win64-shipping.exe": "Delta Force",
    "marvel-win64-shipping.exe": "Marvel Rivals",
}


def game_name(exe: str) -> str:
    """遊戲顯示的名字：認得的用固定的名字，不認得的用程式檔名（去掉 .exe）。"""
    low = (exe or "").lower()
    if low in GAMES:
        return GAMES[low]
    return exe[:-4] if low.endswith(".exe") else exe


def find_game(windows: list[dict], own: set[str] | None = None) -> str:
    """現在在玩哪個遊戲：畫面上開著的視窗（apps.windows()，最上面的在前）裡，
    第一個是認得的遊戲、或使用者替它加過專用詞的程式。回傳程式檔名（小寫），沒有就是空字串。
    看最上面的那一個：同時開著兩個遊戲時，以正在玩（在前面）的為準；切到 Discord 打字時遊戲還開著，照樣算。"""
    own = {o.lower() for o in (own or set())}
    for w in windows:
        exe = str(w.get("exe") or "").lower()
        if exe and (exe in GAMES or exe in own) and not w.get("minimized"):
            return exe
    for w in windows:      # 遊戲都縮到工作列了：還開著也算
        exe = str(w.get("exe") or "").lower()
        if exe and (exe in GAMES or exe in own):
            return exe
    return ""


def packs_for(chosen: list[str], game: str, auto: bool) -> list[str]:
    """實際要用哪幾包。auto 關掉：使用者選的全部用。auto 開著：只跟某個遊戲有關的包，只有那個遊戲開著時才用。"""
    if not auto:
        return [p for p in chosen if p in PACKS]
    return [p for p in chosen if p in PACKS and (p not in GAME_PACKS or game in GAME_PACKS[p])]


class TermPack:
    def __init__(self, names=None, folder: Path | None = None):
        """names：要用哪幾包（不給就是全部）。排前面的包優先。"""
        folder = folder or Path(__file__).resolve().parent / "data"
        self.names = [n for n in (PACKS if names is None else names) if n in PACKS]
        ko_zh, zh_ko = {}, {}
        for name in self.names:
            try:
                raw = json.loads((folder / f"terms_{name}.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                raw = {}
            for src, dst in raw.get("ko_zh", []):
                ko_zh.setdefault(src, dst)
            for src, dst in raw.get("zh_ko", []):
                zh_ko.setdefault(src, dst)
        self.ko_zh = sorted(ko_zh.items(), key=lambda p: len(p[0]), reverse=True)
        self.zh_ko = sorted(zh_ko.items(), key=lambda p: len(p[0]), reverse=True)
        self.jamo = [(src, dst) for src, dst in self.ko_zh if _JAMO_TOKEN.fullmatch(src)]

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
            for token in _JAMO_TOKEN.findall(text):      # ㅊㅋㅊㅋ 這種重複的也算
                for term, dst in self.jamo:
                    if token == term or (len(token) > len(term) and token == term * (len(token) // len(term))):
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
