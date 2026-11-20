"""生成 assets/app.ico（深色圆角底 + 绿色电池）。

需要 Pillow（仅开发者用）。在仓库根目录执行：
    python tools/gen_icon.py
"""

from PIL import Image, ImageDraw

S = 256
BG = (31, 42, 54, 255)
GREEN = (80, 220, 120, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

d.rounded_rectangle([6, 6, S - 6, S - 6], radius=52, fill=BG)

x0, y0, x1, y1 = 44, 96, 190, 160
d.rounded_rectangle([x0, y0, x1, y1], radius=14, outline=GREEN, width=11)
d.rounded_rectangle([x1 + 8, 116, x1 + 22, 140], radius=5, fill=GREEN)

inner_w = int((x1 - x0 - 26) * 0.72)
d.rounded_rectangle([x0 + 13, y0 + 13, x0 + 13 + inner_w, y1 - 13], radius=7, fill=GREEN)

img.save(
    "assets/app.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("saved app.ico")
