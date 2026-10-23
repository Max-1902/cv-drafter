"""Draw the app icon (a CV page with a check mark on the app's teal) as a 1024px PNG. Run: python make_icon.py out.png"""
import sys

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# macOS icon grid: an 824px rounded square centred on the canvas
pad, r = 100, 185
for y in range(pad, S - pad):  # vertical gradient, dark teal to the app's background
    t = (y - pad) / (S - 2 * pad)
    color = (int(22 + (11 - 22) * t), int(124 + (17 - 124) * t), int(149 + (23 - 149) * t), 255)
    d.line([(pad, y), (S - pad, y)], fill=color)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad], r, fill=255)
img.putalpha(mask)
d = ImageDraw.Draw(img)

# the page
x0, y0, x1, y1 = 300, 230, 724, 794
d.rounded_rectangle([x0, y0, x1, y1], 34, fill=(236, 244, 248, 255))
d.rounded_rectangle([x0 + 48, y0 + 60, x0 + 230, y0 + 100], 20, fill=(22, 124, 149, 255))  # name line
for i, w in enumerate((330, 290, 320, 250, 300)):  # text lines
    y = y0 + 160 + i * 62
    d.rounded_rectangle([x0 + 48, y, x0 + 48 + w, y + 26], 13, fill=(160, 181, 194, 255))

# the check badge
cx, cy, cr = 690, 760, 132
d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(55, 183, 210, 255), outline=(11, 17, 23, 255), width=18)
d.line([(cx - 62, cy + 2), (cx - 16, cy + 50), (cx + 66, cy - 46)], fill=(255, 255, 255, 255), width=34, joint="curve")

img.save(sys.argv[1])
