"""工具列圖示上的語言（2.20.0）：圖示角落寫「韓→中」這樣的小字，滑鼠移上去的提示也一樣，
不用打開視窗就知道現在在翻哪兩個語言。"""

SHORT = {
    "zh-TW": {"zh-TW": "中", "zh-CN": "中", "en": "英", "ko": "韓", "ja": "日", "vi": "越", "th": "泰", "id": "印", "es": "西", "pt": "葡", "ru": "俄", "de": "德", "fr": "法"},
    "zh-CN": {"zh-TW": "中", "zh-CN": "中", "en": "英", "ko": "韩", "ja": "日", "vi": "越", "th": "泰", "id": "印", "es": "西", "pt": "葡", "ru": "俄", "de": "德", "fr": "法"},
}
LATIN = {"zh-TW": "ZH", "zh-CN": "ZH", "en": "EN", "ko": "KO", "ja": "JA", "vi": "VI", "th": "TH", "id": "ID", "es": "ES", "pt": "PT", "ru": "RU", "de": "DE", "fr": "FR"}
FONTS = ("msjh.ttc", "msyh.ttc", "malgun.ttf", "YuGothM.ttc", "NotoSansCJK-Regular.ttc", "arial.ttf", "DejaVuSans.ttf")


def short(code: str, ui_lang: str) -> str:
    table = SHORT.get(ui_lang)
    return (table or LATIN).get(code) or str(code)[:2].upper()


def label(their: str, mine: str, ui_lang: str, auto: bool = False) -> str:
    """「韓→中」。對方的語言是自動偵測的就寫「?→中」。"""
    return f"{'?' if auto else short(their, ui_lang)}→{short(mine, ui_lang)}"


def render(base, text: str):
    """在圖示（PIL 圖片）底部加一條寫著 text 的小字。找不到字型就畫不出字，原圖照樣回傳。"""
    from PIL import Image, ImageDraw, ImageFont

    image = base.convert("RGBA").copy()
    w, h = image.size
    font = None
    size = max(10, h // 3)
    for name in FONTS:
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    if font is None:
        return image
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font)
    while (box[2] - box[0]) > w - 2 and size > 8:
        size -= 1
        font = ImageFont.truetype(getattr(font, "path", "arial.ttf"), size)
        box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = (w - tw) // 2 - box[0], h - th - 3 - box[1]
    draw.rectangle((x + box[0] - 2, y + box[1] - 2, x + box[2] + 2, y + box[3] + 2), fill=(15, 17, 19, 235))
    draw.text((x, y), text, font=font, fill=(74, 222, 154, 255))
    return image
