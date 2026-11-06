"""Fast-cut Shorts editor: narration-timed beats over real official shots.

  narration word timings -> section times (hook + beats)
  each section -> cuts of <= max_cut_seconds from its matched shots (topped up from the
  library), at most one AI effect cut -> each cut rendered full-bleed 9:16 with a slow pan
  -> concatenated -> edit.render adds hook text, captions, beat flashes, badges, credit, audio.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

from . import captions
from . import edit
from . import ffmpeg as ff
from . import style

W, H, FPS = edit.W, edit.H, edit.FPS
ACTION_TAGS = {"chase", "police", "explosion", "car", "boat", "helicopter", "gun", "robbery", "beach",
               "night", "neon", "crowd", "swamp", "jason", "lucia"}


@dataclass
class Cut:
    path: str
    kind: str            # video | image | ai
    inpoint: float
    length: float
    shot_id: str | None = None
    pan_left: bool = True
    punch: bool = False   # first cut of a beat: hard zoom-punch for impact


# ---------------------------------------------------------------- timing
def section_times(sections: list[str], word_ts: list[tuple[float, float, str]] | None,
                  total: float) -> list[tuple[float, float]]:
    """Start/end of each narration section. Maps script word counts onto whisper words
    proportionally (robust to "39" vs "thirty-nine"); falls back to word-count share."""
    counts = [max(1, len(s.split())) for s in sections]
    n = sum(counts)
    starts, cum = [], 0
    for c in counts:
        frac = cum / n
        if word_ts:
            idx = min(len(word_ts) - 1, round(frac * len(word_ts)))
            starts.append(0.0 if cum == 0 else word_ts[idx][0])
        else:
            starts.append(frac * total)
        cum += c
    ends = starts[1:] + [total]
    return [(a, max(b, a + 0.3)) for a, b in zip(starts, ends)]


def fallback_words(sections: list[str], times: list[tuple[float, float]]):
    """Evenly spaced words per section when whisper isn't available (captions still work)."""
    out = []
    for text, (a, b) in zip(sections, times):
        ws = text.split()
        step = (b - a) / max(1, len(ws))
        out += [(a + i * step, a + (i + 1) * step, w) for i, w in enumerate(ws)]
    return out


# ---------------------------------------------------------------- planning
def plan(script: dict, times: list[tuple[float, float]], shots_by_id: dict, pool: list[dict],
         ai_files: dict[int, str], cfg) -> list[Cut]:
    """times[0] is the hook, times[1:] are beats."""
    sc = cfg["shorts"]
    used: set[str] = set()
    rng = random.Random(len(pool))
    pool_sorted = sorted(pool, key=lambda s: (s.get("used_count", 0), rng.random()))
    action_first = sorted(pool_sorted, key=lambda s: -len(ACTION_TAGS & set(s.get("tags") or [])))

    def take(preferred: list[dict]) -> dict:
        for s in preferred + pool_sorted:
            if s["id"] not in used:
                used.add(s["id"])
                return s
        s = rng.choice(pool_sorted)  # tiny library: allow repeats
        return s

    cuts: list[Cut] = []
    pan = True
    sections = [{"shot_ids": [], "ai": None}] + [
        {"shot_ids": b.get("shot_ids", []), "ai": ai_files.get(i)} for i, b in enumerate(script["beats"])]
    for si, ((a, b), sec) in enumerate(zip(times, sections)):
        dur = b - a
        ai_len = min(sc["ai_cut_seconds"], dur * 0.5) if sec["ai"] else 0.0
        real = dur - ai_len
        n = max(1, math.ceil(real / sc["max_cut_seconds"]))
        if real / n < sc["min_cut_seconds"] and n > 1:
            n = max(1, int(real // sc["min_cut_seconds"]))
        preferred = [shots_by_id[i] for i in sec["shot_ids"] if i in shots_by_id]
        if si == 0:
            preferred = action_first[:12]   # hook: most dynamic footage first
        seg_cuts = []
        for k in range(n):
            s = take(preferred[k:k + 1] if k < len(preferred) else [])
            ln = real / n
            seg_cuts.append(Cut(s["path"], s["kind"], float(s.get("start") or 0), ln, s["id"], pan,
                                punch=(k == 0)))
            pan = not pan
        if sec["ai"]:
            seg_cuts.insert(len(seg_cuts) // 2 + (1 if len(seg_cuts) > 1 else 0),
                            Cut(sec["ai"], "ai", 0.0, ai_len, None, pan))
        cuts += seg_cuts
    return cuts


# ---------------------------------------------------------------- rendering
GRADE = "eq=contrast=1.12:saturation=1.3:brightness=0.01,unsharp=5:5:0.6,vignette=angle=PI/5"


def render_cut(c: Cut, out: Path) -> Path:
    """One cut, full-bleed 9:16 with motion: punch-zoom on beat starts, slow push otherwise."""
    p0, p1 = (0.35, 0.65) if c.pan_left else (0.65, 0.35)
    n = max(1, round(c.length * FPS))
    if c.punch:
        z = "1.05+0.22*exp(-on/3)"           # slam in then settle
    elif c.pan_left:
        z = f"1.0+0.10*on/{n}"                 # slow push in
    else:
        z = f"1.10-0.10*on/{n}"                # slow pull out
    if c.kind == "image":
        inp = ["-loop", "1", "-t", f"{c.length + 0.5:.3f}", "-i", c.path]
    else:
        inp = ["-ss", f"{c.inpoint:.3f}", "-i", c.path]
    vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H}:x='(iw-{W})*({p0}+({p1 - p0})*min(t/{c.length:.3f}\\,1))':y='(ih-{H})/2',"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},"
          f"{GRADE},setsar=1,tpad=stop_mode=clone:stop_duration={c.length + 1:.2f},format=yuv420p")
    ff.run(inp + ["-an", "-vf", vf, "-t", f"{c.length:.3f}", "-r", str(FPS), "-c:v", "libx264",
                  "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", str(out)])
    return out


def sfx_track(beat_starts: list[float], total: float, work: Path) -> Path:
    """Whoosh into every beat + a low hit on it, synthesised (no licensing issues)."""
    whoosh, hit = work / "whoosh.wav", work / "hit.wav"
    ff.run(["-f", "lavfi", "-i", "anoisesrc=d=0.35:c=pink:r=48000:a=0.7", "-af",
            "highpass=f=350,lowpass=f=7000,afade=t=in:st=0:d=0.27:curve=exp,afade=t=out:st=0.27:d=0.08",
            "-ac", "2", str(whoosh)])
    ff.run(["-f", "lavfi", "-i", "aevalsrc=exprs='0.9*sin(2*PI*(48+110*exp(-t*28))*t)*exp(-t*8)':s=48000:d=0.55",
            "-ac", "2", str(hit)])
    events = [(max(0.0, t - 0.3), whoosh, 0.35) for t in beat_starts] + [(t, hit, 0.55) for t in beat_starts]
    args = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fc = []
    for i, (t, f, vol) in enumerate(events, 1):
        args += ["-i", str(f)]
        ms = int(t * 1000)
        fc.append(f"[{i}:a]volume={vol},adelay={ms}|{ms}[e{i}]")
    mix = "[0:a]" + "".join(f"[e{i}]" for i in range(1, len(events) + 1))
    fc.append(f"{mix}amix=inputs={len(events) + 1}:duration=first:normalize=0[fx]")
    out = work / "sfx.wav"
    ff.run(args + ["-filter_complex", ";".join(fc), "-map", "[fx]", str(out)])
    return out


def concat_copy(parts: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def build(db, cfg, script: dict, voice: Path, ai_files: dict[int, str], work: Path,
          credit: str) -> tuple[Path, bool]:
    """Returns (final video, used_ai)."""
    work.mkdir(parents=True, exist_ok=True)
    pool = db.shots(usable_only=True)
    if not pool:
        raise RuntimeError("official media library is empty: run `gta6mm library sync` first")
    shots_by_id = {s["id"]: s for s in pool}

    total = ff.duration(voice) + 0.35
    sections = [script.get("hook", "")] + [b["narration"] for b in script["beats"]]
    ws = captions.words(voice)
    times = section_times(sections, ws, total)
    cuts = plan(script, times, shots_by_id, pool, ai_files, cfg)

    parts = [render_cut(c, work / f"cut_{i:03d}.mp4") for i, c in enumerate(cuts)]
    base = concat_copy(parts, work / "base.mp4")
    db.mark_used([c.shot_id for c in cuts if c.shot_id])

    srt = work / "captions.srt"
    srt.write_text(captions.to_srt(captions.chunk_words(ws or fallback_words(sections, times), 3)))

    hook_end = times[0][1]
    overlays = [edit.Overlay(script.get("hook_text", "").upper(), 0.0, hook_end + 0.4, y=300, size=150,
                             fg=style.WHITE, bg=style.RED, style="hook", pop=True, rotate=-2.5)]
    for n, ((a, b), beat) in enumerate(zip(times[1:], script["beats"])):
        if beat.get("fact_text"):   # the beat's key fact as a big yellow card
            overlays.append(edit.Overlay(beat["fact_text"].upper(), a, b, y=430, size=130,
                                         fg=style.BLACK, bg=style.YELLOW, style="card", pop=True,
                                         rotate=2.0 if n % 2 else -2.0))
        if beat.get("badge"):
            overlays.append(edit.Overlay(beat["badge"], a, b, y=300, size=52, fg=style.WHITE,
                                         bg=style.RED, style="badge"))
    t, used_ai = 0.0, False
    for c in cuts:
        if c.kind == "ai":
            used_ai = True
        if c.kind == "ai" and cfg["shorts"].get("label_ai_cuts"):
            overlays.append(edit.Overlay("AI EFFECT", t, t + c.length, y=60, size=28, style="badge", x=40))
        t += c.length

    sc = cfg["shorts"]
    music = Path(sc["music_file"]) if sc.get("music_file") and Path(sc["music_file"]).is_file() else None
    out = work / "final.mp4"
    beat_starts = [a for a, _ in times[1:]]
    fx = sfx_track(beat_starts, total, work) if sc.get("sfx", True) else None
    edit.render(edit.RenderSpec(
        src=base, out=out, font=cfg["clipping"]["font"], credit=credit, voiceover=voice,
        caption_words=ws or fallback_words(sections, times), overlays=overlays, source_volume=0.0,
        layout="none", flashes=beat_starts, music=music, music_volume=sc.get("music_volume", 0.12),
        sfx=fx, caption_y=1180, caption_size=100, progress=True))
    return out, used_ai
