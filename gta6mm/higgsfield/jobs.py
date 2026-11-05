"""Higgsfield render queue.

Higgsfield is reached through its MCP connector, which a Claude agent drives
(see .claude/commands/higgsfield-render.md). This module is the contract between
the Python pipeline and that agent:

  queue_script()   script -> 1 assemble job + N hf_video jobs + 1 hf_audio job
  export_pending() writes data/higgsfield/pending.json for the agent
  complete()       agent reports a finished result URL (downloaded locally) or failure
  ready()          assemble jobs whose children are all done -> pipeline renders them

A daily credit budget is enforced at queue time, so a runaway loop can't drain
the account.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .prompts import sanitize


class BudgetExceeded(RuntimeError):
    pass


def _day_start() -> float:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def estimate(script: dict, hf: dict) -> float:
    return len(script["segments"]) * float(hf["est_credits_per_video"]) + float(hf["est_credits_per_voiceover"])


def queue_script(db, cfg, script: dict) -> str:
    hf = cfg["higgsfield"]
    need = estimate(script, hf)
    spent = db.credits_queued_since(_day_start())
    budget = float(hf["daily_credit_budget"])
    if spent + need > budget:
        raise BudgetExceeded(f"need {need} credits, {spent} already queued today, budget {budget}")

    parent = db.add_job("assemble", {"script": script}, status="waiting")
    for i, seg in enumerate(script["segments"]):
        db.add_job("hf_video", {
            "tool": "generate_video", "index": i, "est_credits": float(hf["est_credits_per_video"]),
            "params": {"model": hf["video_model"], "prompt": sanitize(seg["visual_prompt"]),
                       "aspect_ratio": hf["aspect_ratio"], **hf.get("video_params", {})},
        }, parent_id=parent)
    narration = " ".join([script.get("hook", "")] + [s["narration"] for s in script["segments"]]).strip()
    audio_params = {"model": hf.get("voice_model", "seed_audio"), "prompt": narration}
    if hf.get("voice_id"):
        audio_params.update(voice_type="preset", voice_id=hf["voice_id"])
    db.add_job("hf_audio", {"tool": "generate_audio", "est_credits": float(hf["est_credits_per_voiceover"]),
                            "params": audio_params}, parent_id=parent)
    return parent


def pending(db) -> list[dict]:
    out = []
    for kind in ("hf_video", "hf_audio"):
        for j in db.jobs(kind=kind, status="pending") + db.jobs(kind=kind, status="running"):
            out.append({"job_id": j["id"], "status": j["status"], "tool": j["spec"]["tool"],
                        "params": j["spec"]["params"], "est_credits": j["spec"]["est_credits"],
                        "higgsfield_job_id": j["spec"].get("higgsfield_job_id")})
    return out


def export_pending(db, cfg) -> Path:
    path = cfg.data_dir / "higgsfield" / "pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = pending(db)
    path.write_text(json.dumps({"generated_at": time.time(), "count": len(jobs),
                                "est_credits": sum(j["est_credits"] for j in jobs if j["status"] == "pending"),
                                "jobs": jobs}, indent=2))
    return path


def mark_submitted(db, job_id: str, hf_job_id: str) -> None:
    j = db.get_job(job_id)
    spec = j["spec"]
    spec["higgsfield_job_id"] = hf_job_id
    db.update_job(job_id, status="running", spec=spec)


def complete(db, cfg, job_id: str, url: str | None = None, error: str | None = None,
             file: str | None = None) -> None:
    j = db.get_job(job_id)
    if not j:
        raise KeyError(job_id)
    if error:
        db.update_job(job_id, status="failed", error=error)
        if j["parent_id"]:
            db.update_job(j["parent_id"], status="failed", error=f"child {job_id} failed: {error}")
        return
    if file:
        out = Path(file)
    else:
        ext = ".mp3" if j["kind"] == "hf_audio" else ".mp4"
        out = cfg.data_dir / "higgsfield" / "results" / f"{job_id}{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
    spec = j["spec"]
    spec["result_url"] = url
    db.update_job(job_id, status="done", output=str(out), spec=spec)


def ready(db) -> list[dict]:
    res = []
    for parent in db.jobs(kind="assemble", status="waiting"):
        kids = db.jobs(parent_id=parent["id"])
        if kids and all(k["status"] == "done" for k in kids):
            parent["children"] = kids
            res.append(parent)
    return res
