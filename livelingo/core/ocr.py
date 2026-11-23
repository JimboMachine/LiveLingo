"""螢幕文字辨識：用 PP-OCR 模型（ONNX）在本機辨識，並把 Discord 的名字、時間列濾掉。"""

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

TIME_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}")


@dataclass
class Line:
    text: str
    x: int
    y: int
    h: int
    score: float
    w: int = 0      # 這一列有多寬（0 = 不知道；知道寬度才判斷得出「這一行是不是寫滿了才換行」）


def has_hangul(s: str) -> bool:
    return any(0xAC00 <= ord(c) <= 0xD7A3 for c in s)


# 每一種語言用哪一個文字辨識模型（ocr 資料夾裡 <名字>_rec.onnx、<名字>_dict.txt）。沒列的（中文、日文、英文）用 RapidOCR 內建的。
# 越南文的聲調字母（ạ ế ộ …）沒有模型認得，先用拉丁字母的模型：讀得到字，但聲調會少掉一些。
MODELS = {"ko": "korean", "es": "latin", "pt": "latin", "fr": "latin", "de": "latin", "id": "latin", "vi": "latin",
          "ru": "eslav", "th": "thai"}


def model_of(family: str) -> str:
    """family 這個語言用的文字辨識模型名稱；內建的多語言模型叫 builtin。"""
    return MODELS.get(family, "builtin")


class OcrEngine:
    def __init__(self, model_dir: Path | None, scale: float = 1.5, family: str = "ko"):
        """family：要讀的語言。韓文、拉丁字母、俄文、泰文用另外下載的模型；中文、日文、英文用內建的多語言模型。"""
        from rapidocr import RapidOCR  # 延後載入，啟動比較快

        self.scale = scale
        self.family = family
        self.model = model_of(family)
        params = {"Global.use_cls": False, "Global.log_level": "error"}
        if self.model != "builtin":
            if model_dir is None:
                raise RuntimeError(f"OCR model folder for {self.model} is missing")
            params.update({
                "Det.model_path": str(model_dir / "det.onnx"),
                "Rec.model_path": str(model_dir / f"{self.model}_rec.onnx"),
                "Rec.rec_keys_path": str(model_dir / f"{self.model}_dict.txt"),
            })
        self.engine = RapidOCR(params=params)

    def read(self, bgr: np.ndarray) -> list[Line]:
        """回傳畫面上所有文字列（座標已換回原圖大小），由上到下排序。"""
        s = self.scale
        img = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s != 1 else bgr
        res = self.engine(img)
        if res is None or res.txts is None:
            return []
        lines = []
        for box, txt, score in zip(res.boxes, res.txts, res.scores):
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            txt = txt.strip()
            if txt:
                lines.append(Line(txt, int(min(xs) / s), int(min(ys) / s), max(1, int((max(ys) - min(ys)) / s)), float(score),
                                  max(0, int((max(xs) - min(xs)) / s))))
        lines.sort(key=lambda l: (l.y, l.x))
        return lines


def message_lines(lines: list[Line], min_score: float = 0.6) -> list[Line]:
    """
    從辨識結果挑出「訊息內容」：
    - 同一列裡有時間（例如 06:51）的，是 Discord 的「名字 + 時間」標頭，整列跳過
    - 同一列被切成好幾塊的，由左到右接回一句
    """
    rows: list[list[Line]] = []
    for ln in lines:
        if rows and abs(ln.y - rows[-1][0].y) <= 0.6 * max(ln.h, rows[-1][0].h):
            rows[-1].append(ln)
        else:
            rows.append([ln])
    out = []
    for row in rows:
        if any(TIME_RE.search(l.text) for l in row):
            continue
        row.sort(key=lambda l: l.x)
        text = " ".join(l.text for l in row).strip()
        score = min(l.score for l in row)
        if text and score >= min_score:
            right = max(l.x + l.w for l in row) if all(l.w for l in row) else row[0].x
            out.append(Line(text, row[0].x, row[0].y, row[0].h, score, right - row[0].x))
    return out
