"""圖片來源（2.17.0）：拖進來的圖片檔、剪貼簿裡的截圖、網路攝影機拍一張。
這裡只負責把它們變成 BGR 的影像陣列；讀字和翻譯交給 service（跟框選翻譯走同一條路）。全部在這台電腦上，不連網路。"""

import base64
import sys

MAX_BYTES = 15 * 1024 * 1024      # 一張圖最多 15 MB
MAX_SIDE = 4096                   # 太大的圖先縮小：讀字不需要那麼大，也比較快
IS_WIN = sys.platform == "win32"


def shrink(bgr):
    """最長邊超過 MAX_SIDE 的縮小（長條漫畫的長圖不縮，只限制寬）。"""
    import cv2

    h, w = bgr.shape[:2]
    if w > MAX_SIDE:
        bgr = cv2.resize(bgr, (MAX_SIDE, max(1, int(h * MAX_SIDE / w))), interpolation=cv2.INTER_AREA)
    return bgr


def decode(data: bytes):
    """圖片檔的內容（PNG、JPG、BMP、WebP）→ BGR 陣列；不是圖片或太大就是 None。"""
    if not data or len(data) > MAX_BYTES:
        return None
    try:
        import cv2
        import numpy as np

        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None
    if bgr is None or not getattr(bgr, "size", 0):
        return None
    return shrink(bgr)


def from_data_url(text: str):
    """介面送來的 data:image/...;base64,... → BGR 陣列；不對就是 None。"""
    text = str(text or "")
    if not text.startswith("data:image/") or "," not in text or len(text) > MAX_BYTES * 4 // 3 + 200:
        return None
    try:
        data = base64.b64decode(text.split(",", 1)[1], validate=False)
    except (ValueError, TypeError):
        return None
    return decode(data)


def from_pil(image):
    import numpy as np

    rgb = np.asarray(image.convert("RGB"))
    return shrink(np.ascontiguousarray(rgb[:, :, ::-1]))


def clipboard_image():
    """剪貼簿裡的圖（Win+Shift+S 截的、從網頁複製的圖）；剪貼簿裡是圖片檔的話讀第一個。沒有就是 None。不是 Windows 一律 None。"""
    if not IS_WIN:
        return None
    try:
        from PIL import Image, ImageGrab

        got = ImageGrab.grabclipboard()
        if isinstance(got, list):      # 檔案總管裡複製的檔案
            for path in got:
                try:
                    with Image.open(path) as image:
                        return from_pil(image)
                except OSError:
                    continue
            return None
        return from_pil(got) if got is not None else None
    except Exception:  # noqa: BLE001
        return None


def camera_frame(index: int = 0, warmup: int = 8):
    """網路攝影機拍一張（拍完馬上關掉攝影機，不錄影也不存）。打不開就是 None。
    前幾張常常太暗（自動曝光還沒調好），所以丟掉前 warmup 張。"""
    try:
        import cv2

        cam = cv2.VideoCapture(int(index), cv2.CAP_DSHOW) if IS_WIN else cv2.VideoCapture(int(index))
    except Exception:  # noqa: BLE001
        return None
    try:
        if not cam.isOpened():
            return None
        frame = None
        for _ in range(max(1, warmup)):
            ok, got = cam.read()
            if ok and got is not None:
                frame = got
        return shrink(frame) if frame is not None else None
    finally:
        cam.release()
