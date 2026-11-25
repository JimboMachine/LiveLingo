"""
記住的聲音：使用者取過名字的說話者，把名字和聲紋存在這台電腦上（voices.json），下次開程式一開口就認得。
使用者幫他設的語言（主語言 lang、副語言 lang2，語言代碼）也跟著記在同一筆；沒設就是空的。

只有使用者自己打開「記住取過名字的人」才會寫這個檔。關掉那個開關、把某個人的名字清空、或卸載程式，就會刪掉。
這個檔不會傳到任何地方，也不會出現在設定檔、匯出的設定、對話歷史、問題報告裡。
聲紋是 256 個數字，只能拿來比「像不像同一個人」，不能還原成聲音。
"""

import base64
import json
import os
import time
from pathlib import Path

import numpy as np

FILE = "voices.json"
KIND = "livelingo-voices"
MODEL = "wespeaker-voxceleb-resnet34-LM"      # 換了聲紋模型，舊的聲紋就不能比了：整份當作沒有
DIM = 256
MAX_PEOPLE = 20         # 最多記幾個人
MAX_NAME = 24
MAX_LANG = 8            # 語言代碼最長幾個字（"zh-TW"）；是不是真的有這個語言由用的人（service）檢查
MAX_WEIGHT = 30.0       # 記下來的聲紋最多算「說過 30 秒」那麼重：下次換了麥克風或 Discord 的音質，還跟得上新的聲音


def _lang(value) -> str:
    """語言代碼只收短短的英文字母和連字號，其他的當作沒設。"""
    value = value if isinstance(value, str) else ""
    return value if 0 < len(value) <= MAX_LANG and value.replace("-", "").isascii() and value.replace("-", "").isalpha() else ""


def load(data: Path) -> list[dict]:
    """讀出記住的人：[{"name", "voice"（長度是 1 的向量）, "weight", "lang", "lang2"}]。沒有檔案、檔案壞了、是別的模型存的，都回傳空的。"""
    try:
        raw = json.loads((Path(data) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("kind") != KIND or raw.get("model") != MODEL or not isinstance(raw.get("people"), list):
        return []
    people = []
    for row in raw["people"][:MAX_PEOPLE]:
        try:
            name = " ".join(str(row["name"]).split())[:MAX_NAME]
            voice = np.frombuffer(base64.b64decode(row["voice"]), dtype="<f4").astype(np.float32)
            weight = float(row.get("weight") or 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        norm = float(np.linalg.norm(voice)) if voice.shape == (DIM,) and np.all(np.isfinite(voice)) else 0.0
        if name and norm > 0 and np.isfinite(weight):
            people.append({"name": name, "voice": voice / norm, "weight": min(max(weight, 1.0), MAX_WEIGHT),
                           "lang": _lang(row.get("lang")), "lang2": _lang(row.get("lang2"))})
    return people


def save(data: Path, people: list[dict]) -> bool:
    """把這些人存起來（整份換掉）。一個人都沒有就把檔案刪掉。先寫到旁邊再換上去，寫到一半當機不會留下壞檔。"""
    path = Path(data) / FILE
    rows = []
    for person in people[:MAX_PEOPLE]:
        name = " ".join(str(person.get("name") or "").split())[:MAX_NAME]
        voice = np.asarray(person.get("voice"), dtype=np.float32)
        norm = float(np.linalg.norm(voice)) if voice.shape == (DIM,) and np.all(np.isfinite(voice)) else 0.0
        if name and norm > 0:
            rows.append({"name": name, "voice": base64.b64encode((voice / norm).astype("<f4").tobytes()).decode("ascii"),
                         "weight": round(min(max(float(person.get("weight") or 1.0), 1.0), MAX_WEIGHT), 2),
                         **{k: _lang(person.get(k)) for k in ("lang", "lang2") if _lang(person.get(k))}})
    if not rows:
        clear(data)
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(FILE + ".tmp")
        tmp.write_text(json.dumps({"kind": KIND, "version": 1, "model": MODEL, "saved": int(time.time()), "people": rows},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def clear(data: Path) -> None:
    for name in (FILE, FILE + ".tmp"):
        try:
            (Path(data) / name).unlink()
        except OSError:
            pass


def count(data: Path) -> int:
    return len(load(data))
