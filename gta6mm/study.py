"""Study a reference account/video and turn its *format* into a style profile.

  gta6mm study <tiktok/youtube/instagram link>

Downloads up to N recent videos (or the one linked), measures pacing (seconds per
cut, spoken words per second, length), pulls the hook (first ~3s of speech), the
caption/hashtags, and builds a contact sheet. With an Anthropic key, Claude turns
that into data/style_profile.json, which the script writer and editor then follow.

Only the format is learned (structure, pacing, hook style, text style). Their
content and footage are never reused.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from .clipping import captions
from .clipping import ffmpeg as ff
from .library import media

PROFILE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "hook_pattern", "structure", "caption_style", "on_screen_text", "tone",
                 "ending_pattern", "seconds_per_cut", "words_per_second", "target_seconds", "script_guidance"],
    "properties": {
        "summary": {"type": "string"},
        "hook_pattern": {"type": "string", "description": "how the first 2 seconds hook viewers"},
        "structure": {"type": "string", "description": "beat-by-beat structure of a typical video"},
        "caption_style": {"type": "string"},
        "on_screen_text": {"type": "string", "description": "titles/cards/labels used and when"},
        "tone": {"type": "string"},
        "ending_pattern": {"type": "string", "description": "how videos end (CTA, loop, question)"},
        "seconds_per_cut": {"type": "number"},
        "words_per_second": {"type": "number"},
        "target_seconds": {"type": "number"},
        "script_guidance": {"type": "string",
                            "description": "instructions a scriptwriter should follow to match this format"},
    },
}


def _videos(url: str, n: int) -> list[dict]:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": "in_playlist", "playlistend": n}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" or info.get("entries"):
        return [{"url": e.get("url") or e.get("webpage_url"), "id": e.get("id")}
                for e in (info.get("entries") or [])[:n] if e]
    return [{"url": info.get("webpage_url") or url, "id": info.get("id")}]


def contact_sheet(path: Path, out: Path, n: int = 12, cols: int = 6, w: int = 240) -> Path:
    dur = ff.duration(path)
    tiles = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = out.parent / f"_f{i:02d}.jpg"
        ff.run(["-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:-2", str(p)])
        tiles.append(Image.open(p).convert("RGB"))
    th = max(t.height for t in tiles)
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * th), "black")
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * w, (i // cols) * th))
    sheet.save(out, quality=85)
    for p in out.parent.glob("_f*.jpg"):
        p.unlink()
    return out


def measure(video: dict, work: Path) -> dict:
    import yt_dlp

    work.mkdir(parents=True, exist_ok=True)
    opts = {"quiet": True, "noplaylist": True, "format": "b[height<=1280]/bv*+ba/b",
            "outtmpl": str(work / "%(id)s.%(ext)s"), "merge_output_format": "mp4"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video["url"], download=True)
    path = next(work.glob(f"{info['id']}.*"))
    dur = ff.duration(path)
    cuts = media.scene_cuts(path, threshold=0.3)
    words = captions.words(path) or []
    hook = " ".join(w for a, _, w in words if a < 3.0).strip()
    text = " ".join(w for _, _, w in words).strip()
    desc = info.get("description") or info.get("title") or ""
    sheet = contact_sheet(path, work / f"{info['id']}_sheet.jpg")
    return {
        "id": info["id"], "url": info.get("webpage_url"), "uploader": info.get("uploader"),
        "views": info.get("view_count"), "likes": info.get("like_count"),
        "seconds": round(dur, 1), "cuts": len(cuts),
        "seconds_per_cut": round(dur / (len(cuts) + 1), 2),
        "words_per_second": round(len(words) / dur, 2) if dur else 0,
        "hook_first_3s": hook, "transcript": text[:1500],
        "caption": desc[:500], "hashtags": re.findall(r"#\w+", desc),
        "sheet": str(sheet),
    }


def analyse(stats: list[dict], cfg) -> dict | None:
    if not (cfg.env("ANTHROPIC_API_KEY") or cfg.env("ANTHROPIC_AUTH_TOKEN")):
        return None
    import base64

    import anthropic

    content: list[dict] = []
    for s in stats:
        meta = {k: v for k, v in s.items() if k != "sheet"}
        content.append({"type": "text", "text": "Video stats:\n" + json.dumps(meta)})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(Path(s["sheet"]).read_bytes()).decode()}})
    content.append({"type": "text", "text":
                    "These are videos from a reference short-form account (contact sheets show 12 evenly "
                    "spaced frames each). Describe their FORMAT so we can make our own GTA 6 news Shorts "
                    "in the same style: hook, structure, pacing, captions, on-screen text, tone and ending. "
                    "Use the measured numbers for pacing. script_guidance must be concrete instructions for "
                    "a scriptwriter, and must not copy their wording or content."})
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=cfg["scripting"]["model"], max_tokens=16000, betas=["server-side-fallback-2026-07-01"],
        fallbacks="default", thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PROFILE_SCHEMA}},
        messages=[{"role": "user", "content": content}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"analysis stopped: {resp.stop_reason}")
    return json.loads("".join(b.text for b in resp.content if getattr(b, "type", "") == "text"))


def study(url: str, cfg, n: int = 4, log=print) -> dict:
    base = cfg.data_dir / "study"
    stats = []
    for v in _videos(url, n):
        try:
            stats.append(measure(v, base / (v.get("id") or "video")))
            log(f"[study] measured {stats[-1]['url']}: {stats[-1]['seconds']}s, "
                f"{stats[-1]['seconds_per_cut']}s/cut, {stats[-1]['words_per_second']} words/s")
        except Exception as exc:
            log(f"[study] {v.get('url')}: {exc}")
    if not stats:
        raise RuntimeError("could not download any video from that link")
    (base / "stats.json").write_text(json.dumps(stats, indent=2))
    profile = analyse(stats, cfg)
    if profile:
        profile["source"] = url
        (cfg.data_dir / "style_profile.json").write_text(json.dumps(profile, indent=2))
    return {"videos": stats, "profile": profile}


def load_profile(cfg) -> dict | None:
    p = cfg.data_dir / "style_profile.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None
