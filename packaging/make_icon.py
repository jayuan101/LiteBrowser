"""Draws Lite's icon (orange shield with a white check mark) into packaging/lite.ico.

    python packaging\\make_icon.py

Same shape as browser/app.py draws at run time, rendered with Pillow at 16..256 px
so the .exe, taskbar and Explorer all get a crisp icon.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.realpath(__file__))
SIZES = (16, 24, 32, 48, 64, 128, 256)
TOP, BOTTOM = (255, 143, 63), (251, 84, 43)  # gradient, as in app._app_icon


def _shield_points(s: float) -> list[tuple[float, float]]:
    # Control points on a 64-unit grid (app.py), densified into a smooth outline.
    pts = [(32, 3), (57, 12), (57, 30), (51, 45), (32, 61), (13, 45), (7, 30), (7, 12)]
    out = []
    n = len(pts)
    for i in range(n):  # Catmull-Rom through the points, closed
        p0, p1, p2, p3 = (pts[(i + k - 1) % n] for k in range(4))
        for t in (j / 12 for j in range(12)):
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x * s, y * s))
    return out


def render(size: int, oversample: int = 4) -> Image.Image:
    big = size * oversample
    s = big / 64
    mask = Image.new('L', (big, big), 0)
    ImageDraw.Draw(mask).polygon(_shield_points(s), fill=255)
    gradient = Image.new('RGBA', (big, big))
    gpx = gradient.load()
    for y in range(big):
        f = y / (big - 1)
        c = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * f) for i in range(3)) + (255,)
        for x in range(big):
            gpx[x, y] = c
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    img.paste(gradient, mask=mask)
    d = ImageDraw.Draw(img)
    d.line([(20 * s, 33 * s), (29 * s, 42 * s), (45 * s, 23 * s)], fill=(255, 255, 255, 255), width=round(7 * s), joint='curve')
    for cx, cy in ((20, 33), (45, 23)):  # round caps
        r = 3.5 * s
        d.ellipse([cx * s - r, cy * s - r, cx * s + r, cy * s + r], fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def main() -> str:
    path = os.path.join(HERE, 'lite.ico')
    frames = [render(n) for n in SIZES]
    frames[-1].save(path, format='ICO', sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
    frames[-1].save(os.path.join(HERE, 'lite.png'))
    return path


if __name__ == '__main__':
    print('wrote', main())
