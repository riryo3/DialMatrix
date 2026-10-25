"""アイコン（暗い角丸の四角に、緑のリングとダイヤルの印）を作って AppIcon.icns にする。"""
import math, os, subprocess, sys
RING = (70, 255, 100, 255)
RING_GLOW = (60, 255, 90, 255)
OUT = "AppIcon"
from PIL import Image, ImageDraw, ImageFilter
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((64, 64, S - 64, S - 64), 200, fill=(22, 26, 34, 255))
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse((190, 190, S - 190, S - 190), outline=RING_GLOW, width=70)
glow = glow.filter(ImageFilter.GaussianBlur(28))
img = Image.alpha_composite(img, glow)
d = ImageDraw.Draw(img)
d.ellipse((200, 200, S - 200, S - 200), outline=RING, width=34)
d.ellipse((320, 320, S - 320, S - 320), fill=(34, 40, 52, 255), outline=(90, 100, 120, 255), width=10)
c = S / 2
for a in (-90,):                      # ダイヤルの印
    x, y = c + 150 * math.cos(math.radians(a)), c + 150 * math.sin(math.radians(a))
    d.ellipse((x - 26, y - 26, x + 26, y + 26), fill=RING)
if sys.platform == "win32":           # Windows: AppIcon.ico (同じ絵を複数サイズで1ファイルに)
    img.save(OUT + ".ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(OUT + ".ico")
    sys.exit(0)
ICONSET = OUT + ".iconset"
os.makedirs(ICONSET, exist_ok=True)
for sz in (16, 32, 128, 256, 512):
    img.resize((sz, sz), Image.LANCZOS).save(f"{ICONSET}/icon_{sz}x{sz}.png")
    img.resize((sz * 2, sz * 2), Image.LANCZOS).save(f"{ICONSET}/icon_{sz}x{sz}@2x.png")
subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", OUT + ".icns"], check=True)
print(OUT + ".icns")
