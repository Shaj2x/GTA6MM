"""Vertical short renderer (1080x1920) used for both stream clips and AI news videos.

Layout: blurred full-bleed background, sharp source centred, hook title on top,
timed on-screen text, burned captions, credit line and disclosure badge at the
bottom. Voiceover is mixed in with the source audio ducked underneath it.

All text is drawn with Pillow into transparent PNGs and composited with ffmpeg's
core `overlay` filter. That avoids drawtext/subtitles, which many ffmpeg builds
(including Homebrew's default formula) ship without.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg as ff

W, H, FPS = 1080, 1920, 30


@dataclass
class Overlay:
    text: str
    start: float | None = None   # None = whole video
    end: float | None = None
    y: int = 1450
    size: int = 58
    box: bool = True


@dataclass
class RenderSpec:
    src: Path
    out: Path
    font: str
    title: str = ""
    credit: str = ""                 # e.g. "Clip: @streamer on Twitch"
    badge: str = ""                  # e.g. "AI-GENERATED VISUALS" / "RUMOR"
    voiceover: Path | None = None
    captions_srt: Path | None = None
    start: float = 0.0
    max_seconds: float | None = None  # hard cap on how much source is used
    overlays: list[Overlay] = field(default_factory=list)
    source_volume: float = 1.0


@dataclass
class _Layer:
    png: Path
    x: int | str
    y: int
    start: float | None = None
    end: float | None = None


def text_image(lines: list[str], font_path: str, size: int, fg=(255, 255, 255, 255),
               bg=(0, 0, 0, 140), box: bool = True, stroke: int = 3, gap: int = 22) -> Image.Image:
    """Centered block of lines, each on its own rounded box."""
    font = ImageFont.truetype(font_path, size)
    pad = 18 if box else 0
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [probe.textbbox((0, 0), ln, font=font, stroke_width=stroke) for ln in lines]
    widths = [b[2] - b[0] + 2 * pad for b in boxes]
    line_h = size + 2 * pad + stroke * 2
    img_w = min(W, max(widths) if widths else 1)
    img_h = max(1, len(lines) * line_h + (len(lines) - 1) * gap)
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (ln, b, w) in enumerate(zip(lines, boxes, widths)):
        x0 = (img_w - w) // 2
        y0 = i * (line_h + gap)
        if box:
            d.rounded_rectangle([x0, y0, x0 + w, y0 + line_h], radius=14, fill=bg)
        d.text((x0 + pad - b[0], y0 + pad - b[1] + stroke), ln, font=font, fill=fg,
               stroke_width=stroke, stroke_fill=(0, 0, 0, 220))
    return img


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    def t(s: str) -> float:
        h, m, rest = s.strip().split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    out = []
    for block in re.split(r"\n\s*\n", path.read_text().strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = lines[1].split("-->")
            out.append((t(a), t(b), " ".join(lines[2:])))
    return out


def _layers(spec: RenderSpec, total: float, tmp: Path) -> list[_Layer]:
    layers: list[_Layer] = []

    def add(img: Image.Image, x, y, start=None, end=None):
        p = tmp / f"t{len(layers):03d}.png"
        img.save(p)
        layers.append(_Layer(p, x, y, start, end))

    center = "(W-w)/2"
    if spec.title:
        add(text_image(ff.wrap(spec.title, 22, 3), spec.font, 72, bg=(0, 0, 0, 165)), center, 170)
    if spec.badge:
        add(text_image([spec.badge], spec.font, 36, fg=(255, 230, 0, 255), bg=(200, 20, 20, 220)), 40, 60)
    for o in spec.overlays:
        add(text_image(ff.wrap(o.text, 28, 3), spec.font, o.size, box=o.box), center, o.y,
            o.start, o.end if o.end is not None else (total if o.start is not None else None))
    if spec.credit:
        add(text_image([spec.credit], spec.font, 38, bg=(0, 0, 0, 150)), center, H - 160)
    if spec.captions_srt and Path(spec.captions_srt).exists():
        for a, b, txt in parse_srt(Path(spec.captions_srt)):
            add(text_image(ff.wrap(txt, 24, 2), spec.font, 64, fg=(255, 255, 255, 255), box=False,
                           stroke=5), center, 1230, a, b)
    return layers


def build_command(spec: RenderSpec, tmp: Path) -> list[str]:
    src_dur = ff.duration(spec.src) - spec.start
    use = min(src_dur, spec.max_seconds) if spec.max_seconds else src_dur
    vo_dur = ff.duration(spec.voiceover) if spec.voiceover else 0.0
    total = max(use, vo_dur)
    pad = max(0.0, total - use)

    args = ["-ss", f"{spec.start:.3f}", "-t", f"{use:.3f}", "-i", str(spec.src)]
    vo_idx = None
    if spec.voiceover:
        vo_idx = 1
        args += ["-i", str(spec.voiceover)]
    layers = _layers(spec, total, tmp)
    first_png = 1 + (1 if spec.voiceover else 0)
    for layer in layers:
        args += ["-i", str(layer.png)]

    v = [f"[0:v]fps={FPS},setsar=1" + (f",tpad=stop_mode=clone:stop_duration={pad:.3f}" if pad else "")
         + ",split[a][b]",
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
         "eq=brightness=-0.18[bg]",
         f"[b]scale={W}:-2[fg]",
         "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"]
    cur = "v0"
    for i, layer in enumerate(layers):
        nxt = f"v{i + 1}"
        en = ""
        if layer.start is not None:
            en = f":enable='between(t,{layer.start:.2f},{layer.end:.2f})'"
        v.append(f"[{cur}][{first_png + i}:v]overlay=x={layer.x}:y={layer.y}{en}[{nxt}]")
        cur = nxt
    v.append(f"[{cur}]format=yuv420p[vout]")

    a = []
    if ff.has_audio(spec.src):
        a.append(f"[0:a]aresample=48000,volume={spec.source_volume},apad=whole_dur={total:.3f}[sa]")
    else:
        a.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[sa]")
    if vo_idx is not None:
        a += [f"[{vo_idx}:a]aresample=48000,apad=whole_dur={total:.3f},asplit[vo1][vo2]",
              "[sa][vo1]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400[duck]",
              "[duck][vo2]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[aout]"]
    else:
        a.append("[sa]anull[aout]")

    return args + [
        "-filter_complex", ";".join(v + a),
        "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(spec.out),
    ]


def render(spec: RenderSpec) -> Path:
    ff.require()
    spec.font = ff.resolve_font(spec.font)
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = spec.out.parent / f".layers_{spec.out.stem}"
    tmp.mkdir(exist_ok=True)
    try:
        ff.run(build_command(spec, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return spec.out


def concat(segments: list[Path], out: Path) -> Path:
    """Normalise AI segments (any size/fps) into one 1080x1920 silent-safe track."""
    ff.require()
    out.parent.mkdir(parents=True, exist_ok=True)
    args, parts = [], []
    for i, s in enumerate(segments):
        args += ["-i", str(s)]
        parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"fps={FPS},setsar=1,format=yuv420p[v{i}]")
    joined = "".join(f"[v{i}]" for i in range(len(segments)))
    fc = ";".join(parts) + f";{joined}concat=n={len(segments)}:v=1:a=0[v]"
    ff.run(args + ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "18", str(out)])
    return out
