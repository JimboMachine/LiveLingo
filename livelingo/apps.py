"""
現在開著哪些程式（有視窗的）。給「只聽這幾個程式的聲音」「只讀這個程式的視窗」的清單用。
只看視窗標題和程式檔名，不會去讀別的程式的內容。
"""

import os
import sys

IS_WIN = sys.platform == "win32"

# 一定有視窗、但不會有人想選的系統程式
_SKIP = {"applicationframehost.exe", "textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe",
         "startmenuexperiencehost.exe", "systemsettings.exe", "lockapp.exe", "explorer.exe"}


def _fake() -> list[dict] | None:
    """測試用：LIVELINGO_FAKE_APPS="Discord.exe|Discord - 一般;League of Legends.exe|League of Legends" """
    raw = os.environ.get("LIVELINGO_FAKE_APPS")
    if raw is None:
        return None
    out = []
    for n, part in enumerate(p for p in raw.split(";") if p.strip()):
        exe, _, title = part.partition("|")
        out.append({"exe": exe.strip(), "name": display_name(exe.strip()), "title": title.strip(), "pids": [1000 + n],
                    "hwnd": 5000 + n})
    return out


def display_name(exe: str) -> str:
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def _exe_of(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


def windows() -> list[dict]:
    """畫面上看得到、有標題的視窗：[{"hwnd", "pid", "exe", "title"}]，照前後順序（最上面的在前）。"""
    if not IS_WIN:
        return [{"hwnd": a["hwnd"], "pid": a["pids"][0], "exe": a["exe"], "title": a["title"]} for a in _fake() or []]
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    found: list[dict] = []
    own = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        try:
            if not u32.IsWindowVisible(hwnd) or u32.GetWindowLongW(hwnd, -20) & 0x80:      # WS_EX_TOOLWINDOW
                return True
            length = u32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD(0)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value or pid.value == own:
                return True
            exe = _exe_of(pid.value)
            if exe and exe.lower() not in _SKIP:
                found.append({"hwnd": int(hwnd), "pid": int(pid.value), "exe": exe, "title": buf.value})
        except Exception:  # noqa: BLE001
            pass
        return True

    u32.EnumWindows(each, 0)
    return found


def running() -> list[dict]:
    """開著的程式，同一個程式的好幾個視窗合成一列：[{"exe", "name", "title", "pids"}]，照名字排。"""
    fake = _fake()
    if fake is not None:
        return [{k: a[k] for k in ("exe", "name", "title", "pids")} for a in fake]
    if not IS_WIN:
        return []
    rows: dict[str, dict] = {}
    for w in windows():
        row = rows.setdefault(w["exe"].lower(), {"exe": w["exe"], "name": display_name(w["exe"]), "title": w["title"],
                                                 "pids": []})
        if w["pid"] not in row["pids"]:
            row["pids"].append(w["pid"])
    return sorted(rows.values(), key=lambda r: r["name"].lower())


def _processes() -> list[tuple[int, int, str]]:
    """所有程序：[(編號, 上一層程序的編號, 檔名)]。"""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)      # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))
    return out


def roots(processes: list[tuple[int, int, str]], exes: list[str]) -> dict[str, list[int]]:
    """
    這幾個程式各自「最上層」的程序編號。像 Discord、瀏覽器這種一開就是一串程序的，
    聲音其實是下面的子程序在播；收音時指定最上層那個、連同它下面的一起收，才不會漏。
    """
    wanted = {e.lower(): e for e in exes}
    by_exe: dict[str, set[int]] = {e: set() for e in wanted}
    for pid, _parent, name in processes:
        if name.lower() in by_exe:
            by_exe[name.lower()].add(pid)
    out: dict[str, list[int]] = {}
    for pid, parent, name in processes:
        low = name.lower()
        if low in by_exe and parent not in by_exe[low]:
            out.setdefault(wanted[low], []).append(pid)
    return {exe: sorted(pids) for exe, pids in out.items()}


def pids_of(exes: list[str]) -> dict[str, list[int]]:
    """現在開著的這幾個程式的最上層程序編號；沒開的不會出現在結果裡。"""
    fake = _fake()
    if fake is not None:
        wanted = {e.lower() for e in exes}
        return {a["exe"]: list(a["pids"]) for a in fake if a["exe"].lower() in wanted}
    if not IS_WIN or not exes:
        return {}
    return roots(_processes(), exes)
