"""Highlight detection.

Two signals, best first:
  1. YouTube "Most replayed" heatmap (yt-dlp exposes it as info['heatmap']).
  2. Audio energy: loud bursts (screams, explosions, crowd/chat reactions) in a
     stream VOD correlate strongly with clip-worthy moments.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def pick_windows(series: list[tuple[float, float]], clip_len: float, n: int,
                 total: float | None = None) -> list[tuple[float, float]]:
    """series: [(time, value)]. Returns up to n non-overlapping (start, end) windows
    centred on the highest-value points."""
    if not series:
        return []
    total = total or max(t for t, _ in series) + clip_len
    chosen: list[tuple[float, float]] = []
    for t, _ in sorted(series, key=lambda x: x[1], reverse=True):
        start = max(0.0, min(t - clip_len * 0.4, total - clip_len))
        end = min(total, start + clip_len)
        if all(end <= s or start >= e for s, e in chosen):
            chosen.append((round(start, 2), round(end, 2)))
        if len(chosen) >= n:
            break
    return sorted(chosen)


def from_heatmap(heatmap: list[dict] | None, clip_len: float, n: int,
                 total: float | None = None) -> list[tuple[float, float]]:
    if not heatmap:
        return []
    series = [((h["start_time"] + h["end_time"]) / 2, h["value"]) for h in heatmap]
    # skip the first 3% (intro spike is just people starting the video)
    cutoff = (total or series[-1][0]) * 0.03
    series = [p for p in series if p[0] > cutoff]
    return pick_windows(series, clip_len, n, total)


def audio_energy(path: str | Path, step: float = 1.0) -> list[tuple[float, float]]:
    """Per-`step`-second RMS level (dB) via ffmpeg astats."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af",
           f"asetnsamples=n={int(48000 * step)}:p=0,aresample=48000,astats=metadata=1:reset=1,"
           "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    series, t = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
        if m and t is not None:
            v = -120.0 if m.group(1) == "-inf" else float(m.group(1))
            series.append((t, v))
    return series


def from_audio(path: str | Path, clip_len: float, n: int, total: float | None = None):
    series = audio_energy(path)
    if not series:
        return []
    # smooth over ~5s so a single spike doesn't beat a sustained reaction
    k = 5
    smooth = []
    for i, (t, _) in enumerate(series):
        win = series[max(0, i - k // 2): i + k // 2 + 1]
        smooth.append((t, sum(v for _, v in win) / len(win)))
    return pick_windows(smooth, clip_len, n, total)
