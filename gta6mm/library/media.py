"""Official media library: Rockstar's GTA VI trailers + official screenshots, split into shots.

Only official, publicly released Rockstar material goes in here. Leaked footage is
never downloaded or indexed: it is stolen material that Take-Two takes down, and
it would put the channel at risk.

Videos are stored without audio. Rockstar's trailers use licensed songs, and
re-using them triggers Content ID claims that hand your ad revenue to the label.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageStat

from ..clipping import ffmpeg as ff

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def lib_dir(cfg) -> Path:
    d = cfg.data_dir / "library"
    (d / "videos").mkdir(parents=True, exist_ok=True)
    (d / "thumbs").mkdir(parents=True, exist_ok=True)
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- discovery + download
def official_videos(cfg) -> list[dict]:
    """Official GTA VI videos from Rockstar's YouTube channel plus any extra official URLs."""
    import yt_dlp

    lc = cfg["library"]
    rx = re.compile(lc["title_filter"], re.I)
    out = []
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "playlistend": lc["scan_latest"]}) as ydl:
        info = ydl.extract_info(lc["official_channel"], download=False)
    for e in info.get("entries") or []:
        if e.get("title") and rx.search(e["title"]):
            out.append({"id": e["id"], "title": e["title"],
                        "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"})
    for url in lc.get("extra_urls", []):
        out.append({"id": re.sub(r"\W+", "_", url)[-40:], "title": url, "url": url})
    return out


def download(video: dict, dest: Path) -> Path:
    import yt_dlp

    target = dest / f"{video['id']}.mp4"
    if target.exists():
        return target
    opts = {"quiet": True, "noplaylist": True, "format": "bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
            "outtmpl": str(dest / f"{video['id']}.%(ext)s"), "merge_output_format": "mp4"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video["url"]])
    found = sorted(dest.glob(f"{video['id']}.*"))
    if not found:
        raise RuntimeError(f"download produced no file for {video['url']}")
    if found[0] != target:
        found[0].rename(target)
    return target


# ---------------------------------------------------------------- shot detection
def scene_cuts(path: Path, threshold: float = 0.27) -> list[float]:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-an", "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    return sorted(float(m) for m in re.findall(r"pts_time:([\d.]+)", p.stderr))


def split_shots(cuts: list[float], total: float, min_len: float, max_len: float) -> list[tuple[float, float]]:
    bounds = [0.0] + [c for c in cuts if 0 < c < total] + [total]
    shots = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < min_len:
            continue
        # long shots become up to 3 windows so one shot can't dominate
        n = 0
        while a + min_len <= b and n < 3:
            shots.append((round(a, 2), round(min(b, a + max_len), 2)))
            a += max_len
            n += 1
    return shots


def thumb(path: Path, t: float, out: Path) -> Path:
    ff.run(["-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2", str(out)])
    return out


def looks_usable(img_path: Path) -> bool:
    """Reject black frames, fades and logo/rating cards (mostly dark, low detail)."""
    with Image.open(img_path) as im:
        g = im.convert("L")
        st = ImageStat.Stat(g)
        return st.mean[0] > 22 and st.stddev[0] > 18


def index_video(db, cfg, path: Path, asset: str, title: str) -> int:
    lc = cfg["library"]
    total = ff.duration(path)
    shots = split_shots(scene_cuts(path), total, lc["min_shot_seconds"], lc["max_shot_seconds"])
    tdir = lib_dir(cfg) / "thumbs"
    for n, (a, b) in enumerate(shots):
        th = thumb(path, (a + b) / 2, tdir / f"{asset}_{n:03d}.jpg")
        db.add_shot({"id": f"{asset}#{n}", "asset": asset, "kind": "video", "path": str(path), "start": a,
                     "end": b, "thumb": str(th), "title": title, "usable": looks_usable(th)})
    return len(shots)


def index_images(db, cfg) -> int:
    n = 0
    for img in sorted((lib_dir(cfg) / "images").iterdir()):
        if img.suffix.lower() not in IMAGE_EXT or db.has_asset(img.name):
            continue
        db.add_shot({"id": f"{img.name}#0", "asset": img.name, "kind": "image", "path": str(img),
                     "start": 0, "end": 0, "thumb": str(img), "title": "Official screenshot",
                     "usable": looks_usable(img)})
        n += 1
    return n


def sync(db, cfg, log=print) -> dict:
    d = lib_dir(cfg)
    res = {"videos": 0, "shots": 0, "images": 0}
    try:
        vids = official_videos(cfg)
    except Exception as exc:
        log(f"[library] could not list official videos: {exc}")
        vids = []
    for v in vids:
        if db.has_asset(v["id"]):
            continue
        try:
            path = download(v, d / "videos")
            res["shots"] += index_video(db, cfg, path, v["id"], v["title"])
            res["videos"] += 1
            log(f"[library] indexed {v['title']}")
        except Exception as exc:
            log(f"[library] {v['title']}: {exc}")
    res["images"] = index_images(db, cfg)
    return res
