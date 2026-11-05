"""Thin ffmpeg/ffprobe helpers."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


def require() -> None:
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            raise RuntimeError(f"{b} not found on PATH")


def run(args: list[str]) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args[:6])}...\n{p.stderr[-2000:]}")
    return p.stderr


def probe(path: str | Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", str(path)], capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def duration(path: str | Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio(path: str | Path) -> bool:
    return any(s["codec_type"] == "audio" for s in probe(path)["streams"])


def escape_text(s: str) -> str:
    """Escape for drawtext text='...' (used with expansion=none, so % is literal)."""
    s = s.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
    return re.sub(r"[\r\n]+", " ", s)


def wrap(s: str, width: int = 26, max_lines: int = 3) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines
