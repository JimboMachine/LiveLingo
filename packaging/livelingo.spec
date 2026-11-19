# PyInstaller 設定：把 LiveLingo 和它需要的元件打包成一個資料夾（dist/LiveLingo）。
# 用法（在專案根目錄）：pyinstaller packaging/livelingo.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "livelingo" / "ui"), "livelingo/ui"), (str(ROOT / "livelingo" / "data"), "livelingo/data")]
binaries = []
hiddenimports = ["_cffi_backend", "tkinter", "PIL._imagingft"]
# 這些套件會在執行時讀自己資料夾裡的檔案（模型、設定、字典、原生程式庫），要整包帶走
for package in ("rapidocr", "faster_whisper", "ctranslate2", "onnxruntime", "opencc", "soundcard", "webview",
                "clr_loader", "pythonnet", "av", "tokenizers"):
    try:
        d, b, h = collect_all(package)
    except Exception as ex:  # noqa: BLE001
        print(f"collect_all({package}) skipped: {ex}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "pandas", "scipy", "torch", "tensorflow", "IPython", "pytest", "PyQt5", "PyQt6", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveLingo",
    console=False,
    icon=str(ROOT / "packaging" / "livelingo.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LiveLingo")
