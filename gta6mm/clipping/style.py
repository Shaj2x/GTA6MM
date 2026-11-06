"""Visual style for Shorts: fonts, colours and the text elements (hook, fact card, captions).

Fonts are bundled (SIL Open Font License, free for commercial use):
  Anton             - condensed display face for hooks and fact cards
  Montserrat Black  - heavy geometric sans for captions (the classic Shorts look)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ffmpeg as ff

W, H = 1080, 1920
FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

YELLOW = (255, 226, 0, 255)
RED = (232, 28, 45, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


@lru_cache(maxsize=None)
def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        if role == "display":
            return ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), size)
        f = ImageFont.truetype(str(FONTS / "Montserrat-Variable.ttf"), size)
        try:
            f.set_variation_by_name("Black")
        except Exception:
            pass
        return f
    except OSError:  # bundled fonts missing: fall back to a system bold sans
        return ImageFont.truetype(ff.resolve_font(""), size)


def _shadow(img: Image.Image, offset: int = 10, blur: int = 9, alpha: int = 150) -> Image.Image:
    """Soft drop shadow behind an RGBA element (adds depth on busy footage)."""
    pad = offset + blur * 2
    out = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    a = img.split()[3].point(lambda v: v * alpha // 255)
    sh = Image.new("RGBA", img.size, (0, 0, 0, 255))
    sh.putalpha(a)
    out.alpha_composite(sh, (pad + offset // 2, pad + offset))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(img, (pad, pad))
    return out


def _fit(text_lines: list[str], role: str, size: int, stroke: int, max_w: int):
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    while True:
        f = font(role, size)
        boxes = [probe.textbbox((0, 0), ln, font=f, stroke_width=stroke) for ln in text_lines]
        if max((b[2] - b[0] for b in boxes), default=0) <= max_w or size <= 28:
            return f, boxes, size
        size = int(size * 0.93)


def boxed(text: str, role: str = "display", size: int = 110, fg=WHITE, bg=RED, rotate: float = 0.0,
          wrap: int = 16, pad=(34, 16), stroke: int = 0) -> Image.Image:
    """Bold text on a solid slab (hook / fact card / badge), slightly tilted, with shadow."""
    lines = ff.wrap(text, wrap, 3)
    f, boxes, size = _fit(lines, role, size, stroke, W - 120 - 2 * pad[0])
    line_h = max(b[3] - b[1] for b in boxes)
    gap = int(size * 0.08)
    wmax = max(b[2] - b[0] for b in boxes)
    img = Image.new("RGBA", (wmax + 2 * pad[0], len(lines) * line_h + (len(lines) - 1) * gap + 2 * pad[1]),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=max(6, size // 9), fill=bg)
    for i, (ln, b) in enumerate(zip(lines, boxes)):
        x = (img.width - (b[2] - b[0])) // 2 - b[0]
        y = pad[1] + i * (line_h + gap) - b[1]
        d.text((x, y), ln, font=f, fill=fg, stroke_width=stroke, stroke_fill=BLACK)
    if rotate:
        img = img.rotate(rotate, resample=Image.BICUBIC, expand=True)
    return _shadow(img)


def caption(words: list[str], active: int | None, size: int = 96) -> Image.Image:
    """2-3 word caption; the word being spoken is yellow, the rest white, thick outline."""
    stroke = max(8, size // 9)
    words = [w.upper() for w in words]
    text = " ".join(words)
    lines = [text] if len(text) <= 14 else [" ".join(words[: (len(words) + 1) // 2]),
                                            " ".join(words[(len(words) + 1) // 2:])]
    f, boxes, size = _fit(lines, "caption", size, stroke, W - 100)
    line_h = max(b[3] - b[1] for b in boxes)
    gap = int(size * 0.12)
    img = Image.new("RGBA", (max(b[2] - b[0] for b in boxes) + 20, len(lines) * (line_h + gap) + 20),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    idx = 0
    for li, ln in enumerate(lines):
        b = boxes[li]
        x = (img.width - (b[2] - b[0])) // 2 - b[0]
        y = 10 + li * (line_h + gap) - b[1]
        for w in ln.split(" "):
            color = YELLOW if idx == active else WHITE
            d.text((x, y), w, font=f, fill=color, stroke_width=stroke, stroke_fill=BLACK)
            x += d.textlength(w + " ", font=f)
            idx += 1
    return _shadow(img, offset=8, blur=7, alpha=170)


def plain(text: str, size: int = 30, alpha: int = 215) -> Image.Image:
    f = font("caption", size)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    b = d0.textbbox((0, 0), text, font=f, stroke_width=3)
    img = Image.new("RGBA", (b[2] - b[0] + 8, b[3] - b[1] + 8), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4 - b[0], 4 - b[1]), text, font=f, fill=(255, 255, 255, alpha),
                             stroke_width=3, stroke_fill=(0, 0, 0, 200))
    return img
