"""歷史和單字本加密（2.21.0）：用 Windows 的 DPAPI（資料保護 API）把檔案內容加密，只有這個 Windows 帳號在這台電腦上讀得回來，
換一個帳號或把檔案複製到別台電腦就打不開。不用另外記密碼。非 Windows 沒有這個功能（available() 是 False）。
加密的內容開頭是 PREFIX；沒加密的照常讀，所以開關隨時可以切換。"""

import base64
import sys

PREFIX = "enc1:"


def available() -> bool:
    return sys.platform == "win32"


def _blob_api():
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return ctypes, DataBlob


def _call(data: bytes, encrypt: bool) -> bytes | None:
    if not available():
        return None
    try:
        ctypes, DataBlob = _blob_api()
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(data, len(data))
        source = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        target = DataBlob()
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        args = (ctypes.byref(source), "LiveLingo" if encrypt else None, None, None, None, 0, ctypes.byref(target)) if encrypt \
            else (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
        if not fn(*args):
            return None
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            kernel32.LocalFree(target.pbData)
    except Exception:  # noqa: BLE001
        return None


def protect(data: bytes) -> bytes | None:
    return _call(data, True)


def unprotect(blob: bytes) -> bytes | None:
    return _call(blob, False)


def seal(text: str) -> str:
    """文字 → 加密後的一行文字（PREFIX + base64）。加不了（不是 Windows、出錯）回傳 None。"""
    blob = protect(text.encode("utf-8"))
    return None if blob is None else PREFIX + base64.b64encode(blob).decode("ascii")


def is_sealed(text: str) -> bool:
    return str(text).lstrip().startswith(PREFIX)


def unseal(text: str) -> str | None:
    """seal 的相反。打不開（別的帳號、別台電腦、壞掉了）回傳 None。"""
    try:
        raw = base64.b64decode(str(text).strip()[len(PREFIX):], validate=True)
    except ValueError:
        return None
    data = unprotect(raw)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
