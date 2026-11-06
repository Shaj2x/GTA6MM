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

from PIL import Image, ImageDraw

from . import ffmpeg as ff
from . import style

W, H, FPS = 1080, 1920, 30


@dataclass
class Overlay:
    text: str
    start: float | None = None   # None = whole video
    end: float | None = None
    y: int = 1450
    size: int = 58
    box: bool = True
    fg: tuple = (255, 255, 255, 255)
    bg: tuple = (0, 0, 0, 140)
    style: str = "box"           # box | hook | card | badge | plain
    pop: bool = False            # scale-in animation on entry
    rotate: float = 0.0
    x: int | None = None         # None = centred


@dataclass
class RenderSpec:
    src: Path
    out: Path
    font: str
    title: str = ""
    credit: str = ""                 # e.g. "Clip: @streamer on Twitch"
    badge: str = ""
    voiceover: Path | None = None
    captions_srt: Path | None = None
    caption_words: list | None = None  # [(start, end, word)] -> karaoke captions
    start: float = 0.0
    max_seconds: float | None = None  # hard cap on how much source is used
    overlays: list[Overlay] = field(default_factory=list)
    source_volume: float = 1.0
    layout: str = "fit"               # fit = blurred bg + centred source; none = source is already 9:16
    flashes: list[float] = field(default_factory=list)  # white flash at these times (beat changes)
    music: Path | None = None         # optional royalty-free bed, ducked under the voice
    music_volume: float = 0.12
    sfx: Path | None = None           # pre-mixed whoosh/hit track (not ducked)
    caption_y: int = 1230
    caption_size: int = 96
    progress: bool = False            # retention progress bar along the bottom


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


# ---------------------------------------------------------------- graphics track
POP_SCALES = ((0.05, 1.3), (0.10, 1.12))   # (seconds since entry, scale) then 1.0


@dataclass
class _G:
    start: float
    end: float
    make: object                 # callable() -> PIL image at scale 1
    y: int
    x: int | None = None
    pop: bool = False


def _overlay_image(o: Overlay) -> Image.Image:
    if o.style == "hook":
        return style.boxed(o.text, "display", o.size, fg=o.fg, bg=o.bg, rotate=o.rotate, wrap=14)
    if o.style == "card":
        return style.boxed(o.text, "display", o.size, fg=o.fg, bg=o.bg, rotate=o.rotate, wrap=16)
    if o.style == "badge":
        return style.boxed(o.text, "caption", o.size, fg=o.fg, bg=o.bg, wrap=30, pad=(22, 10))
    if o.style == "plain":
        return style.plain(o.text, o.size)
    return style.boxed(o.text, "caption", o.size, fg=o.fg, bg=o.bg if o.box else (0, 0, 0, 0), wrap=26,
                       pad=(22, 12))


def _caption_items(spec: RenderSpec, total: float) -> list[_G]:
    items: list[_G] = []
    if spec.caption_words:
        ws = spec.caption_words
        chunks, cur = [], []
        for w in ws:
            cur.append(w)
            if len(cur) >= 2 or w[2].rstrip().endswith((".", "!", "?", ",")):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        for ci, ch in enumerate(chunks):
            chunk_end = chunks[ci + 1][0][0] if ci + 1 < len(chunks) else min(total, ch[-1][1] + 0.3)
            words = [w[2].strip() for w in ch]
            for wi, w in enumerate(ch):
                a = w[0]
                b = ch[wi + 1][0] if wi + 1 < len(ch) else chunk_end
                if b <= a:
                    continue
                items.append(_G(a, b, (lambda ws_=words, i=wi: style.caption(ws_, i, spec.caption_size)),
                                spec.caption_y, pop=(wi == 0)))
    elif spec.captions_srt and Path(spec.captions_srt).exists():
        for a, b, txt in parse_srt(Path(spec.captions_srt)):
            items.append(_G(a, b, (lambda t=txt: style.caption(t.split(), None, spec.caption_size)),
                            spec.caption_y, pop=True))
    return items


def _graphics(spec: RenderSpec, total: float, tmp: Path) -> Path:
    """Render every text/graphic element into one transparent image sequence (concat list)."""
    items: list[_G] = []
    if spec.title:
        items.append(_G(0, total, lambda: style.boxed(spec.title, "display", 92, bg=(0, 0, 0, 200)), 170))
    if spec.badge:
        items.append(_G(0, total, lambda: style.boxed(spec.badge, "caption", 34, wrap=30, pad=(20, 8)), 60, x=40))
    for o in spec.overlays:
        a = 0.0 if o.start is None else o.start
        b = total if o.end is None else o.end
        items.append(_G(a, b, (lambda o_=o: _overlay_image(o_)), o.y, o.x, o.pop))
    items += _caption_items(spec, total)
    if spec.credit:
        items.append(_G(0, total, lambda: style.plain(spec.credit, 30), H - 112))

    cuts = {0.0, total}
    for it in items:
        cuts.update({max(0.0, it.start), min(total, it.end)})
        if it.pop:
            cuts.update(min(total, it.start + d) for d, _ in POP_SCALES)
    step = 0.2
    if spec.progress:
        cuts.update(i * step for i in range(int(total / step) + 1))
    times = sorted(t for t in cuts if 0 <= t <= total)

    cache: dict = {}
    files: dict = {}
    entries = []
    for t0, t1 in zip(times, times[1:]):
        if t1 - t0 < 1e-3:
            continue
        state = []
        for i, it in enumerate(items):
            if it.start <= t0 < it.end:
                scale = 1.0
                if it.pop:
                    for d, sc in POP_SCALES:
                        if t0 - it.start < d - 1e-6:
                            scale = sc
                            break
                state.append((i, scale))
        prog = int(t0 / step) if spec.progress else -1
        key = (tuple(state), prog)
        if key not in files:
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for i, scale in state:
                it = items[i]
                base = cache.get((i, 1.0))
                if base is None:
                    base = cache[(i, 1.0)] = it.make()
                img = base
                scale = min(scale, max(1.0, (W - 30) / base.width))  # pop never spills off-screen
                if scale != 1.0:
                    img = cache.get((i, scale))
                    if img is None:
                        img = cache[(i, scale)] = base.resize(
                            (int(base.width * scale), int(base.height * scale)), Image.BICUBIC)
                x = (W - img.width) // 2 if it.x is None else it.x
                y = it.y + (base.height - img.height) // 2
                frame.alpha_composite(img, (max(0, x), max(0, y)))
            if spec.progress:
                d = ImageDraw.Draw(frame)
                d.rectangle([0, H - 14, W, H], fill=(0, 0, 0, 110))
                d.rectangle([0, H - 14, int(W * min(1.0, (t0 + step) / total)), H], fill=style.YELLOW)
            p = tmp / f"g{len(files):04d}.png"
            frame.save(p, compress_level=1)
            files[key] = p
        entries.append((files[key], t1 - t0))
    lst = tmp / "graphics.txt"
    body = "".join(f"file '{p.resolve()}'\nduration {d:.4f}\n" for p, d in entries)
    if entries:
        body += f"file '{entries[-1][0].resolve()}'\n"
    lst.write_text(body)
    return lst


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
    music_idx = None
    if spec.music:
        music_idx = 1 + (1 if spec.voiceover else 0)
        args += ["-stream_loop", "-1", "-i", str(spec.music)]
    n_in = 1 + (1 if spec.voiceover else 0) + (1 if spec.music else 0)
    sfx_idx = None
    if spec.sfx:
        sfx_idx = n_in
        args += ["-i", str(spec.sfx)]
        n_in += 1
    g_idx = n_in
    args += ["-f", "concat", "-safe", "0", "-i", str(_graphics(spec, total, tmp))]

    tp = f",tpad=stop_mode=clone:stop_duration={pad:.3f}" if pad else ""
    if spec.layout == "none":
        v = [f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1{tp}[v0]"]
    else:
        v = [f"[0:v]fps={FPS},setsar=1{tp},split[a][b]",
             f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
             "eq=brightness=-0.18[bg]",
             f"[b]scale={W}:-2[fg]",
             "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"]
    v.append(f"[{g_idx}:v]format=rgba,setpts=PTS-STARTPTS[gfx]")
    v.append("[v0][gfx]overlay=0:0:eof_action=repeat[vg]")
    cur = "vg"
    flash = "".join(f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.5:t=fill:enable='between(t,{f:.3f},{f + 0.07:.3f})'"
                    for f in spec.flashes)
    v.append(f"[{cur}]format=yuv420p{flash}[vout]")

    a = []
    if music_idx is not None:
        a.append(f"[{music_idx}:a]aresample=48000,volume={spec.music_volume},atrim=0:{total:.3f},"
                 f"apad=whole_dur={total:.3f}[sa]")
    elif ff.has_audio(spec.src) and spec.source_volume > 0:
        a.append(f"[0:a]aresample=48000,volume={spec.source_volume},apad=whole_dur={total:.3f}[sa]")
    else:
        a.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[sa]")
    if vo_idx is not None:
        a += [f"[{vo_idx}:a]aresample=48000,apad=whole_dur={total:.3f},asplit[vo1][vo2]",
              "[sa][vo1]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400[duck]",
              "[duck][vo2]amix=inputs=2:duration=longest:normalize=0[mix]"]
    else:
        a.append("[sa]anull[mix]")
    if sfx_idx is not None:
        a += [f"[{sfx_idx}:a]aresample=48000,apad=whole_dur={total:.3f}[fx]",
              "[mix][fx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"]
    else:
        a.append("[mix]alimiter=limit=0.95[aout]")

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
