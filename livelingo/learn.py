"""
語言學習（2.10.0）：字幕旁邊標出一兩個生字，按一下收進單字本，之後在「學習」頁複習。全部在這台電腦上（wordbook.json）。

- 挑生字不用模型（翻譯模型挑得很少、常整句丟回來，實測過）：照文字的樣子切出候選字，挑最長的、還沒收過的；
  意思用翻譯模型把那個字翻成我的語言（跟平常翻譯一樣會用到詞彙表和術語包）。
- 中文、泰文沒有空格，切不出字：不自動標，使用者在字幕上選取一段字自己收。日文只挑漢字詞和片假名詞。
- 複習用間隔重複：記得就往後排（1、3、7、21、60 天），忘了就回到明天。
"""

import json
import re
import threading
import time
from pathlib import Path

FILE = "wordbook.json"
MAX_WORDS = 3000
MAX_WORD = 40                       # 一個生字最長幾個字（選取一大段不算生字）
PER_LINE = 2                        # 一句最多標幾個
STEPS = (1, 3, 7, 21, 60)           # 記得了：下一次複習在幾天後（第幾次記得就用第幾格）
AUTO = ("ko", "ja", "en", "es", "pt", "de", "fr", "id", "vi", "ru")      # 會自動標生字的語言

_LATIN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_HANGUL = re.compile(r"[가-힣]+")
_KANJI = re.compile(r"[一-鿿々]{2,}")
_KATAKANA = re.compile(r"[゠-ヿー]{3,}")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]+")
# 韓文字尾常黏著的助詞：拿掉才是那個字本身（정글이 → 정글）
_PARTICLES = ("에서는", "에서", "으로", "에게", "한테", "까지", "부터", "처럼", "보다", "하고", "이랑",
              "은", "는", "이", "가", "을", "를", "에", "도", "로", "랑", "의", "엔", "만", "와", "과")
# 太常見、不值得標的字（英文只挑四個字母以上，所以短的不用列）
_EASY = {"en": set("""about after again also always because been before being better could didn't doesn't don't
    every from going gonna good great have here just know like little make maybe much need never next only other
    really right should some still sure than thank thanks that their them then there these they thing think this
    those time very want wanna well were what when where which while will with would yeah your you're okay
    around back come coming game games guys into look over play playing something anything everyone""".split()),
         "ko": set("""이번 다음 지금 너무 진짜 정말 그냥 우리 내가 나는 너는 근데 그래 그럼 아니 이거 그거 저거 여기 거기 많이 조금
    빨리 같이 해서 하고 했어 있어 없어 좋아 이제 오늘 내일 어제 뭐야 왜냐""".split())}


def family_of(code: str) -> str:
    return "zh" if str(code).startswith("zh") else str(code)


def candidates(text: str, lang: str) -> list[str]:
    """一句話裡值得學的字，最長的在前面。lang 是這句話的語言。不會自動標的語言回傳空的。"""
    lang = family_of(lang)
    text = str(text or "")
    if lang not in AUTO:
        return []
    if lang == "ko":
        words = []
        for w in _HANGUL.findall(text):
            for p in _PARTICLES:
                if w.endswith(p) and len(w) > len(p):
                    w = w[:-len(p)]
                    break
            if len(w) >= 2 and w not in _EASY["ko"]:
                words.append(w)
    elif lang == "ja":
        words = _KANJI.findall(text) + _KATAKANA.findall(text)
    elif lang == "ru":
        words = [w for w in _CYRILLIC.findall(text) if len(w) >= 5]
    else:
        least = 3 if lang == "vi" else 4
        easy = _EASY.get(lang, set())
        words = [w for w in _LATIN.findall(text) if len(w) >= least and w.lower() not in easy]
    out, seen = [], set()
    for w in sorted(words, key=len, reverse=True):      # sorted 是穩定的：一樣長的照出現的順序
        key = w.lower()
        if key not in seen and len(w) <= MAX_WORD:
            seen.add(key)
            out.append(w)
    return out


def _clean(word: str) -> str:
    return re.sub(r"\s+", " ", str(word or "")).strip().strip(".,!?;:\"'“”‘’「」『』()（）[]【】。、！？…")[:MAX_WORD]


class Wordbook:
    """{"words": [{w, lang, meaning, example, example_tr, added, step, due}], "known": {語言: [我說已經會的字]}}"""

    def __init__(self, folder: Path, now=time.time):
        self.path = folder / FILE
        self._now = now
        self._lock = threading.RLock()
        self.words: list[dict] = []
        self.known: dict[str, list[str]] = {}
        self.error = ""
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as ex:
            self.error = str(ex)
            return
        for row in data.get("words", []) if isinstance(data, dict) else []:
            try:
                w, lang = _clean(row["w"]), family_of(row["lang"])
                if w:
                    self.words.append({"w": w, "lang": lang, "meaning": str(row.get("meaning") or "")[:200],
                                       "example": str(row.get("example") or "")[:300], "example_tr": str(row.get("example_tr") or "")[:300],
                                       "added": float(row.get("added") or 0), "step": max(0, int(row.get("step") or 0)),
                                       "due": float(row.get("due") or 0)})
            except (KeyError, TypeError, ValueError):
                continue
        known = data.get("known", {}) if isinstance(data, dict) else {}
        if isinstance(known, dict):
            self.known = {family_of(k): [str(w).lower() for w in v][:MAX_WORDS] for k, v in known.items() if isinstance(v, list)}

    def _save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"words": self.words, "known": self.known}, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self.error = ""
            return True
        except OSError as ex:
            self.error = str(ex)
            return False

    def _find(self, w: str, lang: str) -> dict | None:
        key, lang = _clean(w).lower(), family_of(lang)
        return next((x for x in self.words if x["lang"] == lang and x["w"].lower() == key), None)

    def has(self, w: str, lang: str) -> bool:
        """收過了，或說過已經會了。"""
        with self._lock:
            return self._find(w, lang) is not None or _clean(w).lower() in self.known.get(family_of(lang), [])

    def fresh(self, text: str, lang: str, n: int = PER_LINE) -> list[str]:
        """這句話裡還沒收過、也不是已經會的生字，最多 n 個。"""
        return [w for w in candidates(text, lang) if not self.has(w, lang)][:n]

    def add(self, w: str, lang: str, meaning: str = "", example: str = "", example_tr: str = "") -> dict | None:
        """收進單字本（已經有就更新意思）。回傳那一筆；字是空的回傳 None。"""
        w, lang = _clean(w), family_of(lang)
        if not w:
            return None
        with self._lock:
            row = self._find(w, lang)
            if row is None:
                now = self._now()
                row = {"w": w, "lang": lang, "meaning": "", "example": str(example)[:300], "example_tr": str(example_tr)[:300],
                       "added": now, "step": 0, "due": now}
                self.words.append(row)
                if lang in self.known and w.lower() in self.known[lang]:
                    self.known[lang].remove(w.lower())
                if len(self.words) > MAX_WORDS:
                    self.words = self.words[-MAX_WORDS:]
            if meaning:
                row["meaning"] = str(meaning).strip()[:200]
            self._save()
            return dict(row)

    def remove(self, w: str, lang: str) -> bool:
        with self._lock:
            row = self._find(w, lang)
            if row is None:
                return False
            self.words.remove(row)
            self._save()
            return True

    def know(self, w: str, lang: str) -> None:
        """「這個我會了」：以後不再標這個字（單字本裡有的也拿掉）。"""
        w, lang = _clean(w).lower(), family_of(lang)
        if not w:
            return
        with self._lock:
            row = self._find(w, lang)
            if row is not None:
                self.words.remove(row)
            known = self.known.setdefault(lang, [])
            if w not in known:
                known.append(w)
                del known[:-MAX_WORDS]
            self._save()

    def review(self, w: str, lang: str, remembered: bool) -> dict | None:
        """複習完一個字：記得就往後排，忘了就明天再來。"""
        with self._lock:
            row = self._find(w, lang)
            if row is None:
                return None
            row["step"] = min(row["step"] + 1, len(STEPS)) if remembered else 0
            row["due"] = self._now() + STEPS[max(0, row["step"] - 1)] * 86400 if remembered else self._now() + 86400
            self._save()
            return dict(row)

    def due(self) -> list[dict]:
        """現在該複習的，最早該複習的在前面。"""
        now = self._now()
        with self._lock:
            return sorted((dict(x) for x in self.words if x["due"] <= now), key=lambda x: x["due"])

    def listing(self, q: str = "") -> list[dict]:
        """最新收的在前面。q：字或意思裡有這些字。"""
        words = [w for w in str(q or "").lower().split() if w]
        with self._lock:
            rows = [dict(x) for x in reversed(self.words)]
        if words:
            rows = [r for r in rows if all(w in (r["w"] + "\n" + r["meaning"]).lower() for w in words)]
        return rows

    def clear(self) -> int:
        with self._lock:
            n = len(self.words)
            self.words, self.known = [], {}
            try:
                self.path.unlink(missing_ok=True)
                self.error = ""
            except OSError as ex:
                self.error = str(ex)
            return n

    def state(self) -> dict:
        now = self._now()
        with self._lock:
            return {"count": len(self.words), "due": sum(1 for x in self.words if x["due"] <= now), "error": self.error}
