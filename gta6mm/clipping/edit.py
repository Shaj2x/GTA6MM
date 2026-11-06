"""Vertical short renderer (1080x1920) used for both stream clips and AI news videos.

Layout: blurred full-bleed background, sharp source centred, hook title on top,
timed on-screen text, burned captions, credit line and disclosure badge at the
bottom. Voiceover is mixed in with the source audio ducked underneath it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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


def _draw(font: str, text: str, y: int | str, size: int, box: bool = True,
          enable: str | None = None, color: str = "white", boxcolor: str = "black@0.55",
          x: str = "(w-text_w)/2") -> str:
    f = (f"drawtext=fontfile='{font}':text='{ff.escape_text(text)}':fontsize={size}:"
         f"fontcolor={color}:x={x}:y={y}:borderw=3:bordercolor=black@0.8:expansion=none")
    if box:
        f += f":box=1:boxcolor={boxcolor}:boxborderw=18"
    if enable:
        f += f":enable='{enable}'"
    return f


def build_command(spec: RenderSpec) -> list[str]:
    src_dur = ff.duration(spec.src) - spec.start
    use = min(src_dur, spec.max_seconds) if spec.max_seconds else src_dur
    vo_dur = ff.duration(spec.voiceover) if spec.voiceover else 0.0
    total = max(use, vo_dur)
    pad = max(0.0, total - use)

    args = ["-ss", f"{spec.start:.3f}", "-t", f"{use:.3f}", "-i", str(spec.src)]
    if spec.voiceover:
        args += ["-i", str(spec.voiceover)]

    v = [f"[0:v]fps={FPS},setsar=1" + (f",tpad=stop_mode=clone:stop_duration={pad:.3f}" if pad else "")
         + ",split[a][b]",
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
         "eq=brightness=-0.18[bg]",
         f"[b]scale={W}:-2[fg]",
         "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"]
    draws = []
    for i, line in enumerate(ff.wrap(spec.title, 22, 3)):
        draws.append(_draw(spec.font, line, 170 + i * 92, 72, boxcolor="black@0.65"))
    if spec.badge:
        draws.append(_draw(spec.font, spec.badge, 60, 36, color="yellow", boxcolor="red@0.85",
                           x="40"))
    for o in spec.overlays:
        en = None
        if o.start is not None:
            en = f"between(t,{o.start:.2f},{(o.end if o.end is not None else total):.2f})"
        for i, line in enumerate(ff.wrap(o.text, 28, 3)):
            draws.append(_draw(spec.font, line, o.y + i * (o.size + 22), o.size, o.box, en))
    if spec.credit:
        draws.append(_draw(spec.font, spec.credit, H - 140, 38, boxcolor="black@0.6"))
    chain = "[v0]" + ",".join(draws) if draws else "[v0]null"
    if spec.captions_srt:
        srt = str(spec.captions_srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        chain += (f",subtitles='{srt}':force_style='FontName=DejaVu Sans,Bold=1,Fontsize=13,"
                  "Alignment=2,MarginV=70,Outline=2'")
    v.append(chain + "[vout]")

    has_src_audio = ff.has_audio(spec.src)
    a = []
    if has_src_audio:
        a.append(f"[0:a]aresample=48000,volume={spec.source_volume},apad=whole_dur={total:.3f}[sa]")
    else:
        a.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[sa]")
    if spec.voiceover:
        a += ["[1:a]aresample=48000,apad=whole_dur=" + f"{total:.3f},asplit[vo1][vo2]",
              "[sa][vo1]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400[duck]",
              "[duck][vo2]amix=inputs=2:duration=longest:normalize=0,"
              "alimiter=limit=0.95[aout]"]
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
    spec.font = ff.resolve_font(spec.font).replace(":", "\\:")
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    ff.run(build_command(spec))
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
