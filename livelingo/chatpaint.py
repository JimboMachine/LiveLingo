"""
把翻譯畫成像實況聊天室那樣的浮動留言：彩色的名字、白色的字、黑色描邊，背景完全透明。
這裡只負責「畫成一張圖」，不碰視窗，所以在任何系統上都能測試。
"""

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 名字的顏色：對方說的、螢幕上讀到的、我自己的
NAME_COLORS = {"heard": (196, 148, 255), "screen": (255, 196, 92), "me": (92, 222, 255)}
TEXT_COLOR = (255, 255, 255)
DIM_COLOR = (214, 214, 224)

NAMES = {
    "zh-TW": {"heard": "對方", "screen": "聊天", "me": "我"},
    "en": {"heard": "Them", "screen": "Chat", "me": "Me"},
    "ko": {"heard": "상대", "screen": "채팅", "me": "나"},
    "ja": {"heard": "相手", "screen": "チャット", "me": "自分"},
}
HINTS = {
    "zh-TW": "拖曳移動，拖邊緣調整大小。在 LiveLingo 按「完成」或在這裡點兩下結束。",
    "en": "Drag to move, drag the edges to resize. Press Done in LiveLingo or double-click here to finish.",
    "ko": "드래그해서 옮기고, 가장자리를 끌어 크기를 조절하세요. LiveLingo에서 완료를 누르거나 여기를 더블클릭하면 끝납니다.",
    "ja": "ドラッグで移動、端をドラッグでサイズ変更。LiveLingo の「完了」を押すか、ここをダブルクリックで終了します。",
}
SAMPLES = {
    "zh-TW": [("heard", "打野來一下下路，對面沒有閃現"), ("screen", "這隻蟲的求生慾好強"), ("me", "좋아, 지금 갈게")],
    "en": [("heard", "Jungle, come bot. They have no Flash."), ("screen", "That bug really wants to live"),
           ("me", "좋아, 지금 갈게")],
    "ko": [("heard", "정글 봇 한 번만 와줘, 상대 점멸 없어"), ("screen", "저 벌레 생존력 진짜 좋네"), ("me", "好，我現在過去")],
    "ja": [("heard", "ジャングル、ボットに来て。相手フラッシュないよ"), ("screen", "あの虫、しぶといな"), ("me", "좋아, 지금 갈게")],
}

_WINDOWS_FONTS = {
    # 每一類文字依序試這些字型檔（都是 Windows 內建的粗體）
    "cjk": ["msjhbd.ttc", "msjh.ttc", "msyhbd.ttc", "YuGothB.ttc", "meiryob.ttc"],
    "ja": ["YuGothB.ttc", "meiryob.ttc", "msjhbd.ttc", "msjh.ttc"],
    "ko": ["malgunbd.ttf", "malgun.ttf", "msjhbd.ttc"],
    "latin": ["segoeuib.ttf", "arialbd.ttf", "msjhbd.ttc"],
}
_OTHER_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _font_path(kind: str) -> str | None:
    if sys.platform == "win32":
        folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                   Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
        for name in _WINDOWS_FONTS[kind]:
            for folder in folders:
                if (folder / name).is_file():
                    return str(folder / name)
        return None
    return next((p for p in _OTHER_FONTS if os.path.isfile(p)), None)


@lru_cache(maxsize=64)
def _font(kind: str, size: int):
    path = _font_path(kind)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:   # 很舊的 Pillow
        return ImageFont.load_default()


def _kind(ch: str, japanese: bool) -> str:
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "ko"
    if code >= 0x2E80:
        return "ja" if japanese else "cjk"
    return "latin"


_NO_LINE_START = set("。，、！？；：,.!?;:)]）」』】…~％%")      # 這些標點不會被擠到下一行的開頭
_TOKEN = re.compile(r"[A-Za-z0-9À-ɏ가-힣'’\-_.,!?:;%()]+|\s+|.", re.S)      # 英文和韓文以「詞」為單位換行


def _wrap(parts: list[tuple[str, tuple]], size: int, width: int, japanese: bool) -> list[list[tuple]]:
    """parts 是 [(文字, 顏色)]；回傳一行一行的 [(文字, 字型, 顏色, 寬度)]。中日韓文字可以在任何字之間換行。"""
    lines: list[list[tuple]] = [[]]
    x = 0.0
    for text, color in parts:
        for token in _TOKEN.findall(text.replace("\n", " ")):
            if token.isspace():
                token = " "
                if not lines[-1]:
                    continue
            font = _font(_kind(token[0], japanese), size)
            w = font.getlength(token)
            if x + w > width and lines[-1] and token[0] not in _NO_LINE_START:
                while lines[-1] and lines[-1][-1][0] == " ":
                    x -= lines[-1].pop()[3]
                lines.append([])
                x = 0.0
                if token == " ":
                    continue
            if w > width:      # 一個超長的英文字或網址：硬切
                for ch in token:
                    cw = font.getlength(ch)
                    if x + cw > width and lines[-1]:
                        lines.append([])
                        x = 0.0
                    lines[-1].append((ch, font, color, cw))
                    x += cw
                continue
            lines[-1].append((token, font, color, w))
            x += w
    return [line for line in lines if line] or [[]]


def paint_message(kind: str, name: str, text: str, width: int, size: int, *, original: str = "",
                  backdrop: int = 0, japanese: bool = False) -> Image.Image:
    """畫一則留言：彩色的名字接著白色的內容，太長會換行。回傳寬度固定、高度剛好的透明圖。"""
    size = max(10, int(size))
    stroke = max(2, round(size / 9))
    pad = stroke + max(3, size // 5)
    inner = max(40, width - pad * 2)
    line_h = round(size * 1.38)
    lines = _wrap([(name, NAME_COLORS.get(kind, NAME_COLORS["heard"])), ("  ", TEXT_COLOR), (text, TEXT_COLOR)],
                  size, inner, japanese)
    small = max(10, round(size * 0.72))
    small_lines = _wrap([(original, DIM_COLOR)], small, inner, japanese) if original else []
    small_h = round(small * 1.34)
    height = pad * 2 + len(lines) * line_h + len(small_lines) * small_h

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    y = pad
    for rows, row_h, sw in ((lines, line_h, stroke), (small_lines, small_h, max(2, stroke - 1))):
        for row in rows:
            x = float(pad)
            for token, font, color, w in row:
                if token != " ":
                    draw.text((x, y), token, font=font, fill=(*color, 255), stroke_width=sw, stroke_fill=(0, 0, 0, 255))
                x += w
            y += row_h

    # 字後面一圈柔和的陰影，在亮的遊戲畫面上也看得清楚
    alpha = layer.getchannel("A")
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(max(1.5, size / 7))).point(lambda v: min(255, int(v * 0.85)))
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if backdrop > 0:
        used = max((sum(t[3] for t in row) for row in lines + small_lines), default=0)
        box = (0, 0, min(width, int(used) + pad * 2), height - 1)
        ImageDraw.Draw(out).rounded_rectangle(box, radius=max(4, size // 3),
                                              fill=(12, 12, 18, round(255 * min(100, backdrop) / 100)))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    out.alpha_composite(shadow, (0, max(1, size // 14)))
    out.alpha_composite(layer)
    return out


def _faded(tile: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return tile
    copy = tile.copy()
    copy.putalpha(tile.getchannel("A").point(lambda v: int(v * max(0.0, alpha))))
    return copy


def compose(tiles: list[tuple[Image.Image, float]], size: tuple[int, int]) -> Image.Image:
    """把留言由下往上疊：最新的在最下面，放不下的舊留言在上緣淡出。tiles 是 [(圖, 透明度 0~1)]，舊的在前。"""
    width, height = size
    canvas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    y = height
    for tile, alpha in reversed(tiles):
        y -= tile.height
        if y < 0:
            visible = tile.height + y
            if visible > tile.height * 0.5:      # 露出一半以上就畫出來，但比較淡
                canvas.alpha_composite(_faded(tile, alpha * 0.45).crop((0, -y, tile.width, tile.height)), (0, 0))
            break
        canvas.alpha_composite(_faded(tile, alpha), (0, y))
    return canvas


def edit_frame(canvas: Image.Image, ui_lang: str, size: int) -> Image.Image:
    """調整位置時的樣子：半透明的底、邊框和一行說明，讓人看得到範圍、抓得到邊。"""
    width, height = canvas.size
    out = Image.new("RGBA", (width, height), (16, 18, 26, 150))
    draw = ImageDraw.Draw(out)
    edge = (92, 222, 255, 255)
    draw.rectangle((0, 0, width - 1, height - 1), outline=edge, width=3)
    grip = max(14, size)
    for cx, cy in ((0, 0), (width, 0), (0, height), (width, height)):
        draw.rectangle((cx - grip, cy - grip, cx + grip, cy + grip), fill=edge)
    hint_size = max(12, round(size * 0.62))
    rows = _wrap([(HINTS.get(ui_lang, HINTS["en"]), (235, 240, 255))], hint_size, max(40, width - grip * 2 - 16),
                 ui_lang == "ja")
    y = 8
    for row in rows:
        x = float(grip + 8)
        for token, font, color, w in row:
            if token != " ":
                draw.text((x, y), token, font=font, fill=(*color, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
            x += w
        y += round(hint_size * 1.35)
    out.alpha_composite(canvas)
    return out


def to_bgra_premultiplied(image: Image.Image) -> bytes:
    """Windows 的透明視窗要的格式：藍綠紅順序，而且顏色先乘上透明度。"""
    import numpy as np

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint16)
    alpha = rgba[..., 3:4]
    out = np.empty(rgba.shape, dtype=np.uint8)
    out[..., 0:3] = ((rgba[..., 2::-1] * alpha + 127) // 255).astype(np.uint8)
    out[..., 3] = alpha[..., 0].astype(np.uint8)
    return out.tobytes()
