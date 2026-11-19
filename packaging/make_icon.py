"""畫出 LiveLingo 的圖示（兩個對話框），存成 Windows 用的 .ico。只有改圖示時才需要重跑。"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024


def bubble(draw, box, tail, color, width):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=int((y2 - y1) * 0.22), outline=color, width=width)
    draw.polygon(tail, fill=color)


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=220, fill=(15, 17, 19, 255))
    mint, amber = (74, 222, 154, 255), (242, 184, 75, 255)
    w = 64
    # 後面的對話框（我）
    bubble(d, (430, 420, 860, 720), [(700, 700), (800, 700), (800, 830)], amber, w)
    # 前面的對話框（對方），先用底色蓋掉重疊的地方再畫
    d.rounded_rectangle((150, 190, 650, 530), radius=80, fill=(15, 17, 19, 255))
    bubble(d, (164, 204, 636, 516), [(230, 496), (340, 496), (230, 640)], mint, w)
    for y in (300, 400):
        d.rounded_rectangle((270, y - 22, 530 if y == 300 else 440, y + 22), radius=22, fill=mint)
    out = Path(__file__).resolve().parent / "livelingo.ico"
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
    print(out)


if __name__ == "__main__":
    main()
