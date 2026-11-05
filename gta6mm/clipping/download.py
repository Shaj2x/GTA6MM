"""Source download via yt-dlp (YouTube videos, YouTube/Twitch VODs, Twitch clips)."""
from __future__ import annotations

from pathlib import Path

import yt_dlp


def info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(url, download=False)


def fetch(url: str, out_dir: Path, sections: list[tuple[float, float]] | None = None,
          max_height: int = 1080) -> list[Path]:
    """Download the whole thing, or only the given (start, end) sections."""
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True, "noplaylist": True,
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(section_start)s.%(ext)s" if sections
                       else out_dir / "%(id)s.%(ext)s"),
    }
    if sections:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(None, sections)
        opts["force_keyframes_at_cuts"] = True
    before = set(out_dir.iterdir())
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return sorted(p for p in set(out_dir.iterdir()) - before if p.suffix in (".mp4", ".mkv", ".webm"))
