"""
每一版改了什麼（五種語言），寫在 data/changes.json。
更新提示（「有新版本」的時候）、關於頁、GitHub 上的發行說明都用同一份，不會各說各的。
"""

import json
import sys
from pathlib import Path

from . import __version__
from .paths import resource_dir

FILE = "changes.json"
MAX_VERSIONS, MAX_ITEMS, MAX_CHARS = 30, 8, 240


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in version.split("."))


def clean(raw) -> dict:
    """只留看得懂的部分：{"2.3.0": {"zh-TW": ["…", …], …}, …}。從網路上拿來的也用這個過一次，壞掉的就當作沒有。"""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for version, langs in raw.items():
        if not (isinstance(version, str) and isinstance(langs, dict) and all(p.isdigit() for p in version.split(".")) and len(version) <= 20):
            continue
        entry = {}
        for lang, items in langs.items():
            if isinstance(lang, str) and len(lang) <= 10 and isinstance(items, list):
                lines = [str(i).strip()[:MAX_CHARS] for i in items if isinstance(i, str) and i.strip()][:MAX_ITEMS]
                if lines:
                    entry[lang] = lines
        if entry:
            out[version] = entry
    newest = sorted(out, key=_key, reverse=True)[:MAX_VERSIONS]
    return {v: out[v] for v in newest}


def bundled() -> dict:
    """跟著程式一起裝的那一份（新的在前）。"""
    try:
        return clean(json.loads((resource_dir() / "data" / FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def since(notes: dict, current: str = __version__) -> list[dict]:
    """比 current 新的每一版改了什麼，新的在前：[{"version", "items": {語言: [句子]}}]。"""
    try:
        now = _key(current)
    except ValueError:
        return []
    return [{"version": v, "items": notes[v]} for v in sorted(notes, key=_key, reverse=True) if _key(v) > now]


def release_notes(version: str = __version__) -> str:
    """GitHub 發行頁上的說明：怎麼安裝，加上這一版改了什麼（中文、英文）。"""
    lines = ["下載 LiveLingo-Setup.exe，點兩下安裝。已經安裝過的話，開啟 LiveLingo 時會問你要不要更新到這個版本。", ""]
    entry = bundled().get(version) or {}
    for lang, title in (("zh-TW", "這一版改了什麼"), ("en", "What's new")):
        if entry.get(lang):
            lines += [f"## {title}", "", *[f"- {item}" for item in entry[lang]], ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str]) -> int:
    """發行的時候用：python -m livelingo.changes dist/release-notes.md"""
    Path(argv[0]).write_text(release_notes(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
