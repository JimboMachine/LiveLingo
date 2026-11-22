"""
檢查「實際裝進來的套件」是不是跟 constraints.txt 鎖的一模一樣。

從 requirements.txt 列的套件（加上指令列另外給的，例如 pyinstaller、pytest）出發，
一路找出它們帶進來的每一個套件：沒鎖到的、版本不一樣的都算錯。自動打包會跑這一支，
所以哪天有套件偷偷多帶了一個新的相依，打包會直接紅，不會默默換掉版本。

用法：python packaging/check_pins.py [另外要查的套件…]
"""

import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(text: str) -> dict[str, str]:
    """「名字==版本」一行一個；# 開頭是註解。別的寫法（>=、沒寫版本）直接算錯。"""
    pins = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9._-]+)==([A-Za-z0-9.!+_-]+)", line)
        if not m:
            raise ValueError(f"not pinned to one exact version: {line}")
        pins[norm(m.group(1))] = m.group(2)
    return pins


def _installed(name: str):
    try:
        return metadata.version(name), metadata.requires(name) or []
    except metadata.PackageNotFoundError:
        return None


def _wanted(requirement: str) -> str | None:
    """這一條相依在這台電腦上要不要裝（看後面的條件）；要的話回傳套件名字。額外功能（extra）才用到的不算。"""
    from packaging.requirements import Requirement

    req = Requirement(requirement)
    if req.marker is not None and not req.marker.evaluate({"extra": ""}):
        return None
    return req.name


def problems(roots: list[str], pins: dict[str, str], installed=_installed, wanted=_wanted) -> list[str]:
    out, seen, todo = [], set(), [norm(r) for r in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        found = installed(name)
        if found is None:
            continue            # 這個平台不用裝的（例如只有 Windows 才要的）
        version, requires = found
        if name not in pins:
            out.append(f"{name} {version} is installed but not pinned in constraints.txt")
        elif pins[name] != version:
            out.append(f"{name}: constraints.txt says {pins[name]}, installed is {version}")
        for requirement in requires:
            dep = wanted(requirement)
            if dep:
                todo.append(norm(dep))
    return sorted(out)


def main(argv: list[str]) -> int:
    pins = read_pins((ROOT / "constraints.txt").read_text(encoding="utf-8"))
    direct = read_pins((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    bad = [f"{n}: requirements.txt says {v}, constraints.txt says {pins.get(n)}" for n, v in direct.items() if pins.get(n) != v]
    bad += problems([*direct, *argv], pins)
    for line in bad:
        print(line)
    print(f"{len(bad)} problem(s); {len(pins)} packages pinned")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
