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

# 名字的顏色：對方說的、螢幕上讀到的、我自己的；note 是程式自己的短提示（例如按了快速鍵：麥克風開了）
MENTION_COLOR = (255, 112, 112)      # 提到我的那一則，名字用這個顏色（喊我名字提醒）
NAME_COLORS = {"heard": (196, 148, 255), "screen": (255, 196, 92), "me": (92, 222, 255), "note": (74, 222, 154)}
NOTE_NAME = "LiveLingo"
# 分辨說話者：第 1、2、3… 個人的名字顏色（輪著用）。避開「我」的青色和「聊天」的橘色，免得看錯是誰。
SPEAKER_COLORS = [(196, 148, 255), (124, 227, 139), (255, 143, 179), (127, 168, 255), (242, 227, 107), (255, 138, 115)]
SPEAKER = {"zh-TW": "說話者 {n}", "zh-CN": "说话者 {n}", "en": "Speaker {n}", "ko": "화자 {n}", "ja": "話者 {n}", "vi": "Người nói {n}", "th": "ผู้พูด {n}", "id": "Pembicara {n}", "es": "Hablante {n}", "pt": "Falante {n}", "ru": "Говорящий {n}", "de": "Sprecher {n}", "fr": "Intervenant {n}"}


def speaker_name(n: int, ui_lang: str, given: str = "") -> str:
    """第 n 個說話者要顯示的名字：使用者取過名字就用那個，不然是「說話者 n」。"""
    return given.strip() or (SPEAKER.get(ui_lang) or SPEAKER["en"]).format(n=n)


def speaker_color(n: int) -> tuple:
    return SPEAKER_COLORS[(max(1, int(n)) - 1) % len(SPEAKER_COLORS)]
TEXT_COLOR = (255, 255, 255)
DIM_COLOR = (214, 214, 224)

NAMES = {
    "zh-TW": {"heard": "對方", "screen": "聊天", "me": "我"},
    "zh-CN": {"heard": "对方", "screen": "聊天", "me": "我"},
    "en": {"heard": "Them", "screen": "Chat", "me": "Me"},
    "ko": {"heard": "상대", "screen": "채팅", "me": "나"},
    "ja": {"heard": "相手", "screen": "チャット", "me": "自分"},
    "vi": {"heard": "Họ", "screen": "Chat", "me": "Tôi"},
    "th": {"heard": "อีกฝ่าย", "screen": "แชท", "me": "ฉัน"},
    "id": {"heard": "Mereka", "screen": "Chat", "me": "Aku"},
    "es": {"heard": "Ellos", "screen": "Chat", "me": "Yo"},
    "pt": {"heard": "Eles", "screen": "Chat", "me": "Eu"},
    "ru": {"heard": "Они", "screen": "Чат", "me": "Я"},
    "de": {"heard": "Andere", "screen": "Chat", "me": "Ich"},
    "fr": {"heard": "Eux", "screen": "Chat", "me": "Moi"},
}
HINTS = {
    "zh-TW": "拖曳移動，拖邊緣調整大小。在 LiveLingo 按「完成」或在這裡點兩下結束。",
    "zh-CN": "拖动可以移动，拖动边缘可以调整大小。在 LiveLingo 按「完成」或在这里双击结束。",
    "en": "Drag to move, drag the edges to resize. Press Done in LiveLingo or double-click here to finish.",
    "ko": "드래그해서 옮기고, 가장자리를 끌어 크기를 조절하세요. LiveLingo에서 완료를 누르거나 여기를 더블클릭하면 끝납니다.",
    "ja": "ドラッグで移動、端をドラッグでサイズ変更。LiveLingo の「完了」を押すか、ここをダブルクリックで終了します。",
    "vi": "Kéo để di chuyển, kéo mép để đổi kích thước. Nhấn Xong trong LiveLingo hoặc nhấp đúp vào đây để kết thúc.",
    "th": "ลากเพื่อย้าย ลากขอบเพื่อปรับขนาด กด เสร็จ ใน LiveLingo หรือดับเบิลคลิกที่นี่เพื่อจบ",
    "id": "Seret untuk memindahkan, seret tepinya untuk mengubah ukuran. Tekan Selesai di LiveLingo atau klik dua kali di sini untuk selesai.",
    "es": "Arrastra para mover, arrastra los bordes para cambiar el tamaño. Pulsa Listo en LiveLingo o haz doble clic aquí para terminar.",
    "pt": "Arraste para mover, arraste as bordas para mudar o tamanho. Clique em Concluir no LiveLingo ou dê um clique duplo aqui para terminar.",
    "ru": "Перетащите, чтобы переместить, потяните за края, чтобы изменить размер. Нажмите «Готово» в LiveLingo или дважды щёлкните здесь, чтобы закончить.",
    "de": "Ziehen zum Verschieben, an den Rändern ziehen zum Ändern der Größe. In LiveLingo auf Fertig klicken oder hier doppelklicken, um abzuschließen.",
    "fr": "Faites glisser pour déplacer, tirez les bords pour redimensionner. Cliquez sur Terminé dans LiveLingo ou double-cliquez ici pour finir.",
}
SAMPLES = {
    "zh-TW": [("heard", "打野來一下下路，對面沒有閃現"), ("screen", "這隻蟲的求生慾好強"), ("me", "좋아, 지금 갈게")],
    "zh-CN": [("heard", "打野来一下下路，对面没有闪现"), ("screen", "这只虫的求生欲好强"), ("me", "좋아, 지금 갈게")],
    "en": [("heard", "Jungle, come bot. They have no Flash."), ("screen", "That bug really wants to live"),
           ("me", "좋아, 지금 갈게")],
    "ko": [("heard", "정글 봇 한 번만 와줘, 상대 점멸 없어"), ("screen", "저 벌레 생존력 진짜 좋네"), ("me", "好，我現在過去")],
    "ja": [("heard", "ジャングル、ボットに来て。相手フラッシュないよ"), ("screen", "あの虫、しぶといな"), ("me", "좋아, 지금 갈게")],
    "vi": [("heard", "Đi rừng ơi xuống bot giúp cái, bên kia không có Tốc Biến"), ("screen", "Con bọ đó lì thật"), ("me", "좋아, 지금 갈게")],
    "th": [("heard", "ป่ามาบอทหน่อย ฝั่งนั้นไม่มีแฟลช"), ("screen", "แมลงตัวนั้นอึดจริงๆ"), ("me", "좋아, 지금 갈게")],
    "id": [("heard", "Jungle, ke bot dong. Lawan nggak ada Flash."), ("screen", "Serangga itu bandel banget"), ("me", "좋아, 지금 갈게")],
    "es": [("heard", "Jungla, ven a bot. No tienen Flash."), ("screen", "Ese bicho no se quiere morir"), ("me", "좋아, 지금 갈게")],
    "pt": [("heard", "Jungle, vem bot. Eles estão sem Flash."), ("screen", "Esse bicho não quer morrer de jeito nenhum"), ("me", "좋아, 지금 갈게")],
    "ru": [("heard", "Лесник, приходи на бот. У них нет флеша."), ("screen", "Этот жук никак не хочет умирать"), ("me", "좋아, 지금 갈게")],
    "de": [("heard", "Jungle, komm bot. Die haben kein Flash."), ("screen", "Der Käfer will einfach nicht sterben"), ("me", "좋아, 지금 갈게")],
    "fr": [("heard", "Jungle, viens bot. Ils n'ont pas de Flash."), ("screen", "Cet insecte ne veut vraiment pas mourir"), ("me", "좋아, 지금 갈게")],
}

_WINDOWS_FONTS = {
    # 每一類文字依序試這些字型檔（都是 Windows 內建的）。sans = 黑體，serif = 明體，kai = 楷體
    ("sans", True): {
        "cjk": ["msjhbd.ttc", "msjh.ttc", "msyhbd.ttc", "YuGothB.ttc", "meiryob.ttc"],
        "ja": ["YuGothB.ttc", "meiryob.ttc", "msjhbd.ttc", "msjh.ttc"],
        "sc": ["msyhbd.ttc", "msyh.ttc", "msjhbd.ttc", "msjh.ttc"],      # 簡體字用微軟雅黑，繁體字它也都有
        "ko": ["malgunbd.ttf", "malgun.ttf", "msjhbd.ttc"],
        "latin": ["segoeuib.ttf", "arialbd.ttf", "msjhbd.ttc"],
        "th": ["LeelaUIb.ttf", "leelawdb.ttf", "tahomabd.ttf"],      # 泰文：微軟正黑體、Segoe UI 都沒有泰文字
    },
    ("sans", False): {
        "cjk": ["msjh.ttc", "msyh.ttc", "YuGothM.ttc", "meiryo.ttc"],
        "ja": ["YuGothM.ttc", "YuGothR.ttc", "meiryo.ttc", "msjh.ttc"],
        "sc": ["msyh.ttc", "msjh.ttc"],
        "ko": ["malgun.ttf", "msjh.ttc"],
        "latin": ["segoeui.ttf", "arial.ttf", "msjh.ttc"],
        "th": ["LeelawUI.ttf", "leelawad.ttf", "tahoma.ttf"],
    },
    ("serif", False): {
        "cjk": ["mingliub.ttc", "mingliu.ttc", "simsun.ttc"],
        "ja": ["yumin.ttf", "msmincho.ttc", "mingliub.ttc"],
        "sc": ["simsun.ttc", "mingliub.ttc"],
        "ko": ["batang.ttc"],
        "latin": ["georgia.ttf", "times.ttf"],
        "th": [],
    },
    ("kai", False): {
        "cjk": ["kaiu.ttf", "simkai.ttf"],
        "ja": ["kaiu.ttf"],
        "sc": ["simkai.ttf", "kaiu.ttf"],
        "ko": [],
        "latin": [],
        "th": [],
    },
}
FAMILIES = ("sans", "serif", "kai")
ALIGNS = ("left", "center", "right")
GROWS = ("up", "down")
_OTHER_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
# 不是 Windows 時，泰文另外找有泰文字的字型（開發、測試用）
_OTHER_THAI = ["/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
               "/usr/share/fonts/opentype/tlwg/Loma-Bold.otf", "/usr/share/fonts/opentype/tlwg/Loma.otf"]


def _font_path(kind: str, family: str = "sans", bold: bool = True) -> str | None:
    if sys.platform == "win32":
        folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                   Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
        # 明體和楷體沒有另外的粗體檔（粗體是畫的時候加粗）；這台電腦沒有那個字型就退回黑體
        names = _WINDOWS_FONTS[(family, bold) if family == "sans" else (family, False)][kind]
        for name in [*names, *_WINDOWS_FONTS[("sans", bold)][kind], *_WINDOWS_FONTS[("sans", True)][kind]]:
            for folder in folders:
                if (folder / name).is_file():
                    return str(folder / name)
        return None
    return next((p for p in (_OTHER_THAI if kind == "th" else []) + _OTHER_FONTS if os.path.isfile(p)), None)


@lru_cache(maxsize=None)
def _draw_heavier(kind: str, family: str, bold: bool) -> bool:
    """明體、楷體沒有粗體檔，要粗體時畫的時候把筆畫加粗。這台電腦沒有那個字型、退回黑體粗體時就不用再加。"""
    if not bold or family == "sans" or sys.platform != "win32":
        return False
    path = _font_path(kind, family, bold) or ""
    return Path(path).name in _WINDOWS_FONTS[(family, False)][kind]


@lru_cache(maxsize=96)
def _font(kind: str, size: int, family: str = "sans", bold: bool = True):
    path = _font_path(kind, family, bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:   # 很舊的 Pillow
        return ImageFont.load_default()


def _rgb(value) -> tuple:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", str(value or ""))
    if not m:
        raise ValueError(value)
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))


DEFAULT_STYLE = {"family": "sans", "bold": True, "color": "#FFFFFF", "outline": 2, "names": True,
                 "name_colors": {"heard": "#C494FF", "screen": "#FFC45C", "me": "#5CDEFF"},
                 "opacity": 100, "align": "left", "grow": "up", "lines": 0}


def style_of(cfg) -> dict:
    """設定裡跟浮動留言長相有關的那幾項（設定叫 overlay_family，這裡叫 family）。少了哪一項就用預設值。"""
    style = {}
    for key, default in DEFAULT_STYLE.items():
        try:
            value = cfg["overlay_" + key]
        except KeyError:
            value = default
        style[key] = dict(default, **value) if isinstance(default, dict) and isinstance(value, dict) else value
    return style


def style_key(style: dict | None) -> tuple:
    """同樣的長相得到同樣的值，用來判斷畫好的留言能不能重複使用。"""
    style = style or {}
    return tuple((k, tuple(sorted(v.items())) if isinstance(v, dict) else v) for k, v in sorted(style.items()))


def han_kind(*langs: str | None) -> str:
    """漢字要用哪一套字型：有日文用日文的，有簡體中文用簡體的，其他用繁體的。"""
    if "ja" in langs:
        return "ja"
    return "sc" if "zh-CN" in langs else "cjk"


def _kind(ch: str, han: str) -> str:
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "ko"
    if code >= 0x2E80:
        return han
    if 0x0E00 <= code <= 0x0E7F:
        return "th"
    return "latin"


_NO_LINE_START = set("。，、！？；：,.!?;:)]）」』】…~％%")      # 這些標點不會被擠到下一行的開頭
# 英文、韓文、俄文、越南文以「詞」為單位換行；泰文詞和詞之間不空格，以一個字（連同上面、下面的母音和聲調符號）為單位
_TOKEN = re.compile(r"[A-Za-z0-9À-ɏЀ-ӿ\u0300-\u036fḀ-ỿ가-힣'’\-_.,!?:;%()]+|[\u0e00-\u0e7f][\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]*|\s+|.",
                    re.S)


def _wrap(parts: list[tuple[str, tuple]], size: int, width: int, han: str = "cjk", family: str = "sans",
          bold: bool = True) -> list[list[tuple]]:
    """parts 是 [(文字, 顏色)]；回傳一行一行的 [(文字, 字型, 顏色, 寬度)]。中日韓文字可以在任何字之間換行。"""
    lines: list[list[tuple]] = [[]]
    x = 0.0
    for text, color in parts:
        for token in _TOKEN.findall(text.replace("\n", " ")):
            if token.isspace():
                token = " "
                if not lines[-1]:
                    continue
            font = _font(_kind(token[0], han), size, family, bold)
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
                  backdrop: int = 0, han: str = "cjk", style: dict | None = None, name_color: tuple | None = None) -> Image.Image:
    """
    畫一則留言：彩色的名字接著內容，太長會換行。回傳寬度固定、高度剛好的透明圖。
    style（都可以省略，見 DEFAULT_STYLE）：family 字型、bold 粗體、color 字的顏色、outline 描邊 0 到 4、
    names 要不要顯示名字、name_colors 三種名字的顏色、align 靠左／置中／靠右。
    name_color：這一則的名字用這個顏色（分辨說話者時，每個人一個顏色），沒給就照種類。
    """
    style = {**DEFAULT_STYLE, **(style or {})}
    family = style["family"] if style["family"] in FAMILIES else "sans"
    bold = bool(style["bold"])
    align = style["align"] if style["align"] in ALIGNS else "left"
    try:
        outline = max(0, min(4, int(style["outline"])))
    except (TypeError, ValueError):
        outline = 2
    try:
        text_color = _rgb(style["color"])
    except ValueError:
        text_color = TEXT_COLOR
    dim_color = DIM_COLOR if text_color == TEXT_COLOR else tuple((c * 3 + 150) // 4 for c in text_color)
    name_colors = dict(NAME_COLORS)
    for key, value in (style["name_colors"] or {}).items():
        try:
            name_colors[key] = _rgb(value)
        except ValueError:
            pass

    size = max(10, int(size))
    stroke = round(size / 9 * (0, 0.45, 1, 1.25, 1.5)[outline])      # outline 2 = 原本的粗細
    extra = max(1, size // 22)      # 明體、楷體沒有粗體檔：筆畫自己加粗這麼多
    pad = max(2, stroke) + max(3, size // 5)
    inner = max(40, width - pad * 2)
    line_h = round(size * 1.38)
    parts = [(text, text_color)]
    if style["names"] and name:
        parts = [(name, name_color or name_colors.get(kind, name_colors["heard"])), ("  ", text_color), *parts]
    lines = _wrap(parts, size, inner, han, family, bold)
    small = max(10, round(size * 0.72))
    small_lines = _wrap([(original, dim_color)], small, inner, han, family, bold) if original else []
    small_h = round(small * 1.34)
    height = pad * 2 + len(lines) * line_h + len(small_lines) * small_h
    used = max((sum(t[3] for t in row) for row in lines + small_lines), default=0)

    def start(row_width: float) -> float:
        return float(pad) if align == "left" else (width - row_width) / 2 if align == "center" else width - pad - row_width

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    y = pad
    for rows, row_h, sw in ((lines, line_h, stroke), (small_lines, small_h, max(1, stroke - 1) if stroke else 0)):
        for row in rows:
            x = start(sum(t[3] for t in row))
            for token, font, color, w in row:
                if token != " ":
                    heavy = extra if _draw_heavier(_kind(token[0], han), family, bold) else 0
                    if sw or heavy:
                        draw.text((x, y), token, font=font, fill=(0, 0, 0, 255) if sw else (*color, 255),
                                  stroke_width=sw + heavy, stroke_fill=(0, 0, 0, 255) if sw else (*color, 255))
                    draw.text((x, y), token, font=font, fill=(*color, 255), stroke_width=heavy, stroke_fill=(*color, 255))
                x += w
            y += row_h

    # 字後面一圈柔和的陰影，在亮的遊戲畫面上也看得清楚
    alpha = layer.getchannel("A")
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(max(1.5, size / 7))).point(lambda v: min(255, int(v * 0.85)))
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if backdrop > 0:
        left = max(0, int(start(used)) - pad)
        box = (left, 0, min(width, left + int(used) + pad * 2), height - 1)
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


def compose(tiles: list[tuple[Image.Image, float]], size: tuple[int, int], *, grow: str = "up", opacity: int = 100,
            lines: int = 0) -> Image.Image:
    """
    把留言疊成一張圖。tiles 是 [(圖, 透明度 0~1)]，舊的在前。
    grow：up = 最新的在最下面，舊的往上推；down = 從最上面開始往下排，最新的在最下面那一則。
    放不下的舊留言在邊緣淡出。opacity 是整體不透明度（30 到 100），lines 是最多顯示幾則（0 = 放得下就顯示）。
    """
    width, height = size
    canvas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    strength = max(0.3, min(1.0, opacity / 100))
    if lines > 0:
        tiles = tiles[-lines:]
    if grow == "down":
        while len(tiles) > 1 and sum(t.height for t, _ in tiles) > height:      # 放不下：最舊的先讓位
            tiles = tiles[1:]
        y = 0
        for tile, alpha in tiles:
            canvas.alpha_composite(_faded(tile, alpha * strength), (0, y))
            y += tile.height
        return canvas
    y = height
    for tile, alpha in reversed(tiles):
        y -= tile.height
        if y < 0:
            visible = tile.height + y
            if visible > tile.height * 0.5:      # 露出一半以上就畫出來，但比較淡
                canvas.alpha_composite(_faded(tile, alpha * strength * 0.45).crop((0, -y, tile.width, tile.height)), (0, 0))
            break
        canvas.alpha_composite(_faded(tile, alpha * strength), (0, y))
    return canvas


def preview(cfg, size: tuple[int, int] = (640, 230)) -> Image.Image:
    """設定畫面上的預覽：把範例留言照現在的設定畫在一張假的遊戲畫面上（左邊暗、右邊亮，兩種底都看得到效果）。"""
    width, height = size
    lang = cfg["ui_lang"] or "en"
    names = NAMES.get(lang, NAMES["en"])
    style = style_of(cfg)
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((width, height))      # 左暗右亮
    scene = Image.merge("RGB", [ramp.point(lambda v, lo=lo, hi=hi: lo + (hi - lo) * v // 255)
                                for lo, hi in ((22, 150), (34, 196), (44, 214))]).convert("RGBA")
    box = (16, 12, width - 16, height - 12)
    tiles = [(paint_message(kind, names[kind], text, box[2] - box[0], int(cfg["overlay_font"]),
                            original=SAMPLES["en"][n][1] if cfg["overlay_original"] and SAMPLES["en"][n][1] != text else "",
                            backdrop=int(cfg["overlay_backdrop"]), han=han_kind(cfg["my_lang"], cfg["their_lang"], lang),
                            style=style), 1.0)
             for n, (kind, text) in enumerate(SAMPLES.get(lang, SAMPLES["en"])) if kind != "me" or cfg["overlay_mine"]]
    chat = compose(tiles, (box[2] - box[0], box[3] - box[1]), grow=style["grow"], opacity=int(style["opacity"]),
                   lines=int(style["lines"]))
    scene.alpha_composite(chat, (box[0], box[1]))
    return scene.convert("RGB")


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
                 han_kind(ui_lang))
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
