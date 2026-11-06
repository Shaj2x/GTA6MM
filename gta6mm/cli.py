"""gta6mm command line."""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import config, pipeline
from .higgsfield import jobs as hf


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gta6mm", description="GTA 6 launch content engine")
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="pull trending videos/streams/clips/news and score them")
    t = sub.add_parser("trending", help="show top-scored sources")
    t.add_argument("-n", type=int, default=20)
    sub.add_parser("outreach", help="permission DMs for trending creators you can't clip yet")
    sub.add_parser("clip", help="plan + render clips for rights-cleared sources")
    cu = sub.add_parser("clip-url", help="clip a specific YouTube/Twitch/TikTok link (rights-gated)")
    cu.add_argument("urls", nargs="+")
    vo = sub.add_parser("clip-voiceover", help="attach your commentary track to a needs_voiceover job")
    vo.add_argument("job_id")
    vo.add_argument("wav")
    n = sub.add_parser("news", help="write a sourced script and queue Higgsfield jobs")
    n.add_argument("--force", action="store_true")

    lib = sub.add_parser("library", help="official Rockstar media library")
    lib.add_argument("action", choices=["sync", "tag", "stats"])
    hp = sub.add_parser("hf-pending", help="print Higgsfield jobs for the agent")
    hp.add_argument("--brief", action="store_true", help="one line per job")
    s = sub.add_parser("hf-submitted", help="record the Higgsfield job id for a queued job")
    s.add_argument("job_id")
    s.add_argument("higgsfield_job_id")
    c = sub.add_parser("hf-complete", help="record a finished Higgsfield result")
    c.add_argument("job_id")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--file")
    g.add_argument("--error")
    cx = sub.add_parser("hf-cancel", help="cancel a queued AI short (assemble-... id)")
    cx.add_argument("parent_id")
    rb = sub.add_parser("rebuild", help="re-edit a finished short with the current style (no credits)")
    rb.add_argument("parent_id", nargs="?", help="assemble-... id (default: most recent)")
    sub.add_parser("assemble", help="build finished AI shorts whose segments are done")

    r = sub.add_parser("review", help="list posts awaiting approval")
    r.add_argument("--status", default="awaiting_approval")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("post_ids", nargs="+")
    sub.add_parser("publish", help="upload approved posts (YouTube) and write the TikTok queue")
    tk = sub.add_parser("tiktok-done", help="mark a TikTok post published by the agent")
    tk.add_argument("post_id")
    tk.add_argument("--publish-id", default="")

    sub.add_parser("status", help="pipeline counts")
    sub.add_parser("stats", help="views on your published YouTube posts")
    run = sub.add_parser("run", help="full cycle; --every N minutes to loop")
    run.add_argument("--every", type=float, default=0)

    a = ap.parse_args(argv)
    cfg = config.load(a.config)
    if cfg.get("launch_mode") and a.cmd == "run":
        print("[launch mode] GTA 6 is out: clip-first settings active")
    db = pipeline.open_db(cfg)

    if a.cmd == "discover":
        pipeline.discover(db, cfg)
    elif a.cmd == "trending":
        _print([{k: s[k] for k in ("score", "platform", "kind", "channel", "views", "title",
                                    "rights_mode", "url")} for s in db.top_sources(limit=a.n)])
    elif a.cmd == "outreach":
        _print(pipeline.outreach(db, cfg))
    elif a.cmd == "clip":
        pipeline.plan_clips(db, cfg)
        pipeline.process_clips(db, cfg)
    elif a.cmd == "clip-url":
        for u in a.urls:
            _print(pipeline.add_url(db, cfg, u))
        pipeline.process_clips(db, cfg)
    elif a.cmd == "clip-voiceover":
        pipeline.attach_voiceover(db, a.job_id, a.wav)
        pipeline.process_clips(db, cfg)
    elif a.cmd == "news":
        pipeline.news_cycle(db, cfg, force=a.force)
    elif a.cmd == "library":
        from .library import media, tagger
        if a.action == "sync":
            print(media.sync(db, cfg, log=pipeline.log))
            tagger.tag_all(db, cfg, log=pipeline.log)
        elif a.action == "tag":
            tagger.tag_all(db, cfg, log=pipeline.log)
        all_shots = db.shots(usable_only=False)
        _print({"assets": len({s["asset"] for s in all_shots}), "shots": len(all_shots),
                "usable": sum(s["usable"] for s in all_shots),
                "tagged": sum(1 for s in all_shots if s["description"]),
                "folder_for_screenshots": str(media.lib_dir(cfg) / "images")})
    elif a.cmd == "hf-pending":
        hf.export_pending(db, cfg)
        jobs = hf.pending(db)
        if a.brief:
            for j in jobs:
                p = j["params"]
                print(f"\n{j['job_id']}  ({j['tool']})\n  {p.get('prompt') or ''}")
            if not jobs:
                print("(nothing pending)")
        else:
            _print(jobs)
    elif a.cmd == "hf-submitted":
        hf.mark_submitted(db, a.job_id, a.higgsfield_job_id)
    elif a.cmd == "hf-complete":
        hf.complete(db, cfg, a.job_id, url=a.url, error=a.error, file=a.file)
        pipeline.assemble_ready(db, cfg)
    elif a.cmd == "hf-cancel":
        print(f"cancelled {hf.cancel(db, a.parent_id)} queued segments")
        hf.export_pending(db, cfg)
    elif a.cmd == "rebuild":
        done = [j for j in db.jobs(kind="assemble") if j["status"] in ("done", "failed")]
        target = a.parent_id or (done[-1]["id"] if done else None)
        if not target:
            print("no finished short to rebuild")
            return 1
        db.update_job(target, status="waiting")
        for p in db.posts():
            if p["job_id"] == target and p["status"] == "awaiting_approval":
                db.update_post(p["id"], status="rejected")   # replaced by the rebuilt version
        pipeline.assemble_ready(db, cfg)
    elif a.cmd == "assemble":
        pipeline.assemble_ready(db, cfg)
    elif a.cmd == "review":
        _print([{k: p[k] for k in ("id", "platform", "file", "title", "synthetic")}
                for p in db.posts(status=a.status)])
    elif a.cmd in ("approve", "reject"):
        for pid in a.post_ids:
            db.update_post(pid, status="approved" if a.cmd == "approve" else "rejected")
        print(f"{a.cmd}d {len(a.post_ids)}")
    elif a.cmd == "publish":
        pipeline.publish(db, cfg)
    elif a.cmd == "tiktok-done":
        db.update_post(a.post_id, status="published", external_id=a.publish_id)
    elif a.cmd == "status":
        q = lambda sql: db.conn.execute(sql).fetchall()  # noqa: E731
        _print({
            "sources": dict(q("SELECT kind, COUNT(*) FROM sources GROUP BY kind")),
            "rights": dict(q("SELECT COALESCE(rights_mode,'-'), COUNT(*) FROM sources GROUP BY 1")),
            "jobs": {f"{k}/{s}": c for k, s, c in q("SELECT kind, status, COUNT(*) FROM jobs GROUP BY 1,2")},
            "posts": {f"{p}/{s}": c for p, s, c in q("SELECT platform, status, COUNT(*) FROM posts GROUP BY 1,2")},
            "hf_credits_queued_today": db.credits_queued_since(hf._day_start()),
            "hf_daily_budget": cfg["higgsfield"]["daily_credit_budget"],
        })
    elif a.cmd == "stats":
        from .discovery import youtube
        key = cfg.env("YOUTUBE_API_KEY")
        ids = [p["external_id"] for p in db.posts(status="published", platform="youtube")]
        if not key or not ids:
            print("need YOUTUBE_API_KEY and at least one published YouTube post")
            return 1
        st = youtube.stats(key, ids)
        rows = sorted(({"id": i, **st.get(i, {})} for i in ids), key=lambda r: -r.get("viewCount", 0))
        _print(rows)
    elif a.cmd == "run":
        while True:
            pipeline.cycle(db, cfg)
            if not a.every:
                break
            time.sleep(a.every * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
