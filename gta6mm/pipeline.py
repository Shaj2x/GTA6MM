"""Orchestration: discover -> rights -> clip -> script -> Higgsfield -> assemble -> review -> publish."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import rights
from .clipping import captions, edit, highlights
from .clipping import ffmpeg as ff
from .db import DB
from .discovery import news, scoring, twitch, youtube
from .higgsfield import jobs as hf
from .publish import metadata


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def open_db(cfg) -> DB:
    return DB(cfg.data_dir / "gta6mm.sqlite")


# --------------------------------------------------------------------------- discovery
def discover(db: DB, cfg) -> dict:
    counts = {}
    for name, mod in (("youtube", youtube), ("twitch", twitch), ("news", news)):
        try:
            items = mod.discover(cfg)
        except Exception as exc:
            log(f"discover/{name} failed: {exc}")
            items = []
        for s in items:
            s["score"] = scoring.score(s) if s["kind"] != "article" else 0.0
            if s["kind"] != "article" and s["score"] <= 0:
                continue
            db.upsert_source(s)
            d = rights.evaluate(s, cfg["rights"])
            db.set_rights(s["id"], d.mode, d.reason)
        counts[name] = len(items)
    log(f"discovered {counts}")
    return counts


def outreach(db: DB, cfg, limit: int = 15) -> list[dict]:
    """Top trending creators we can't use yet, with a ready-to-send permission DM."""
    seen, out = set(), []
    for s in db.top_sources(limit=200):
        if s["rights_mode"] != "blocked" or s["channel"] in seen:
            continue
        seen.add(s["channel"])
        out.append({"platform": s["platform"], "channel": s["channel"], "score": s["score"],
                    "views": s["views"], "url": s["url"],
                    "message": rights.outreach_message(s, cfg["publish"]["channel_handle"])})
        if len(out) >= limit:
            break
    path = cfg.data_dir / "outreach.json"
    path.write_text(json.dumps(out, indent=2))
    return out


# --------------------------------------------------------------------------- clipping
def plan_clips(db: DB, cfg) -> list[str]:
    created = []
    cap = cfg["clipping"]["max_new_clip_jobs_per_cycle"]
    for s in db.top_sources(kinds=("video", "clip", "stream"), limit=100):
        if len(created) >= cap:
            break
        d = rights.evaluate(s, cfg["rights"])
        if not d.allowed or db.has_job_for_source(s["id"], "clip"):
            continue
        if s["kind"] == "stream":  # live: clip from the VOD once it exists; skip for now
            continue
        status = "needs_voiceover" if d.constraints.get("require_voiceover") else "pending"
        created.append(db.add_job("clip", {"url": s["url"], "mode": d.mode, "attribution": d.attribution,
                                           "constraints": d.constraints}, source_id=s["id"], status=status))
    log(f"planned {len(created)} clip jobs")
    return created


def _hook(title: str) -> str:
    t = re.sub(r"[#|\[\]【】].*$", "", title or "").strip()
    t = re.sub(r"\s{2,}", " ", t)
    return t[:80] or "GTA 6 moment you have to see"


def process_clip_job(db: DB, cfg, job: dict) -> list[str]:
    from .clipping import download  # yt-dlp import is slow; keep lazy

    spec, src = job["spec"], db.get_source(job["source_id"])
    decision = rights.evaluate(src, cfg["rights"])
    if not decision.allowed:  # rights may have been revoked since planning
        db.update_job(job["id"], status="blocked", error=decision.reason)
        return []
    work = cfg.data_dir / "clips" / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    db.update_job(job["id"], status="running")
    try:
        info = download.info(spec["url"])
        c = cfg["clipping"]
        total = float(info.get("duration") or 0)
        # parts: (file, start offset inside file, seconds to use)
        if src["kind"] == "clip" or (total and total <= c["clip_seconds"] * 1.5):
            f = download.fetch(spec["url"], work / "src")[0]
            parts = [(f, 0.0, ff.duration(f))]
        else:
            windows = highlights.from_heatmap(info.get("heatmap"), c["clip_seconds"],
                                              c["clips_per_source"], total)
            if windows:  # "most replayed" peaks: download only those sections
                files = download.fetch(spec["url"], work / "src", sections=windows)
                parts = [(f, 0.0, b - a) for f, (a, b) in zip(files, windows)]
            else:  # no heatmap: whole VOD at 720p, then loudest moments
                f = download.fetch(spec["url"], work / "src", max_height=720)[0]
                windows = highlights.from_audio(f, c["clip_seconds"], c["clips_per_source"],
                                                ff.duration(f))
                parts = [(f, a, b - a) for a, b in windows]
        outs = []
        cap = decision.constraints.get("max_source_seconds")
        vo = Path(spec["voiceover"]) if spec.get("voiceover") else None
        platform = {"twitch": "Twitch", "youtube": "YouTube"}.get(src["platform"], src["platform"])
        for i, (f, start, length) in enumerate(parts):
            use = min(length, cap) if cap else length
            srt = captions.transcribe(f, work / f"{i}.srt", start, use) if c.get("captions") else None
            out = work / f"clip_{i}.mp4"
            edit.render(edit.RenderSpec(
                src=f, out=out, font=c["font"], title=_hook(src["title"]),
                credit=f"{decision.attribution or '@' + str(src['channel'])} on {platform}",
                voiceover=vo, captions_srt=srt, start=start, max_seconds=use,
                source_volume=0.6 if vo else 1.0))
            outs.append(str(out))
        db.update_job(job["id"], status="done", output=json.dumps(outs))
        meta = metadata.for_clip(src, decision, cfg, _hook(src["title"]))
        return [_make_posts(db, cfg, job["id"], o, meta) for o in outs]
    except Exception as exc:
        db.update_job(job["id"], status="failed", error=str(exc)[-1500:])
        log(f"clip {job['id']} failed: {exc}")
        return []


def process_clips(db: DB, cfg) -> int:
    n = 0
    for job in db.jobs(kind="clip", status="pending"):
        n += len(process_clip_job(db, cfg, job))
    log(f"rendered {n} clips")
    return n


def attach_voiceover(db: DB, job_id: str, wav: str) -> None:
    j = db.get_job(job_id)
    spec = j["spec"]
    spec["voiceover"] = str(Path(wav).resolve())
    db.update_job(job_id, spec=spec, status="pending")


# --------------------------------------------------------------------------- news shorts
def news_cycle(db: DB, cfg, force: bool = False) -> str | None:
    from .scripting import writer

    shots = db.shots(usable_only=True)
    if not shots:
        log("official media library is empty: run `gta6mm library sync` first")
        return None
    since = time.time() - cfg["discovery"]["lookback_hours"] * 3600
    items = db.news(since)
    if not items:
        log("no news items in window")
        return None
    digest = hashlib.sha1(",".join(sorted(i["id"] for i in items)).encode()).hexdigest()
    state = cfg.data_dir / "last_news_digest"
    if not force and state.exists() and state.read_text() == digest:
        log("no new news since last script")
        return None
    script = writer.write(items, shots, cfg)
    try:
        parent = hf.queue_script(db, cfg, script)
    except hf.BudgetExceeded as exc:
        log(f"higgsfield budget: {exc}")
        return None
    state.write_text(digest)
    path = hf.export_pending(db, cfg)
    n_ai = sum(1 for b in script["beats"] if b.get("ai_effect_prompt"))
    log(f"queued short {parent}: {len(script['beats'])} beats, narration + {n_ai} AI effect -> {path}")
    return parent


def assemble_ready(db: DB, cfg) -> int:
    from .clipping import timeline

    n = 0
    for parent in hf.ready(db):
        script = parent["spec"]["script"]
        if "beats" not in script:  # made by the old all-AI format
            db.update_job(parent["id"], status="cancelled", error="old all-AI format; regenerate with `news --force`")
            continue
        kids = parent["children"]
        ai_files = {k["spec"]["index"]: k["output"] for k in kids if k["kind"] == "hf_video"}
        audio = next((Path(k["output"]) for k in kids if k["kind"] == "hf_audio"), None)
        work = cfg.data_dir / "shorts" / parent["id"]
        try:
            credit = f"Footage: Rockstar Games \u00b7 {metadata.countdown(cfg['release_date'])}"
            out, used_ai = timeline.build(db, cfg, script, audio, ai_files, work, credit)
            db.update_job(parent["id"], status="done", output=str(out))
            meta = metadata.for_news(script, cfg)
            meta["synthetic"] = used_ai
            if used_ai:
                meta["description"] += "\nOne stylised effect shot is AI-generated."
            _make_posts(db, cfg, parent["id"], str(out), meta)
            n += 1
            log(f"short ready: {out} ({ff.duration(out):.1f}s)")
        except Exception as exc:
            db.update_job(parent["id"], status="failed", error=str(exc)[-1500:])
            log(f"assemble {parent['id']} failed: {exc}")
    log(f"assembled {n} shorts")
    return n


# --------------------------------------------------------------------------- publishing
def _make_posts(db: DB, cfg, job_id: str, file: str, meta: dict) -> str:
    status = "approved" if cfg["publish"]["auto_approve"] else "awaiting_approval"
    pid = ""
    for platform in ("youtube", "tiktok"):
        pid = db.add_post(job_id, platform, file, meta["title"], meta["description"], meta["tags"],
                          meta["synthetic"], status)
    return pid


def publish(db: DB, cfg) -> dict:
    from .publish import youtube_upload

    res = {"youtube": 0, "tiktok_manifest": 0}
    for p in db.posts(status="approved", platform="youtube"):
        try:
            vid = youtube_upload.upload(cfg, p)
            db.update_post(p["id"], status="published", external_id=vid)
            res["youtube"] += 1
        except Exception as exc:
            db.update_post(p["id"], status="failed", error=str(exc)[-800:])
            log(f"youtube upload {p['id']} failed: {exc}")
    tiktok = [{"post_id": p["id"], "file": str(Path(p["file"]).resolve()), "title": p["title"][:150],
               "description": p["description"][:2000], "is_aigc": bool(p["synthetic"])}
              for p in db.posts(status="approved", platform="tiktok")]
    path = cfg.data_dir / "higgsfield" / "tiktok_queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tiktok, indent=2))
    res["tiktok_manifest"] = len(tiktok)
    log(f"published {res}")
    return res


# --------------------------------------------------------------------------- loop
def library_sync(db: DB, cfg, every_hours: float = 12) -> None:
    """Pull new official Rockstar media (twice a day) and describe new shots."""
    from .library import media, tagger

    stamp = cfg.data_dir / "last_library_sync"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < every_hours * 3600:
        return
    res = media.sync(db, cfg, log=log)
    tagger.tag_all(db, cfg, log=log)
    stamp.write_text(str(time.time()))
    log(f"library sync {res}")


def cycle(db: DB, cfg) -> None:
    library_sync(db, cfg)
    discover(db, cfg)
    outreach(db, cfg)
    plan_clips(db, cfg)
    process_clips(db, cfg)
    news_cycle(db, cfg)
    hf.export_pending(db, cfg)
    assemble_ready(db, cfg)
    publish(db, cfg)
