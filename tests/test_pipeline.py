"""End-to-end tests with synthetic media (needs ffmpeg) and no network."""
import json
import subprocess
from pathlib import Path

import pytest

from gta6mm import config, pipeline
from gta6mm.clipping import ffmpeg as ff
from gta6mm.higgsfield import jobs as hf


def mk_video(path: Path, seconds: float, size="1280x720", audio=True):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-shortest"]
    subprocess.run(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def mk_audio(path: Path, seconds: float):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=200:duration={seconds}",
                    str(path)], check=True)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "YOUTUBE_API_KEY", "TWITCH_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "config.yaml").write_text(
        "data_dir: data\nclipping: {captions: false}\nhiggsfield: {daily_credit_budget: 30}\n"
        "rights:\n  licensed_creators:\n    - {platform: twitch, channel: streamer, evidence: 'DM 11/04', attribution: '@streamer'}\n")
    cfg = config.load()
    return cfg, pipeline.open_db(cfg), tmp_path


def add_news(db, n=3):
    import time
    for i in range(n):
        db.upsert_source({"id": f"news:{i}", "platform": "news", "kind": "article", "url": f"https://x/{i}",
                          "title": f"GTA 6 headline {i}", "channel": "Press", "published_at": time.time() - 60,
                          "meta": {"tier": ["official", "press", "community"][i % 3], "summary": "s"}})


def test_news_to_higgsfield_to_assembled_post(env):
    cfg, db, tmp = env
    add_news(db, 3)
    parent = pipeline.news_cycle(db, cfg)
    assert parent
    pend = hf.pending(db)
    assert {p["tool"] for p in pend} == {"generate_video", "generate_audio"}
    assert all("GTA" not in p["params"]["prompt"] for p in pend if p["tool"] == "generate_video")
    assert json.loads((tmp / "data/higgsfield/pending.json").read_text())["count"] == len(pend)
    # same news again -> no duplicate script
    assert pipeline.news_cycle(db, cfg) is None

    # agent reports results (local files instead of URLs)
    for p in pend:
        hf.mark_submitted(db, p["job_id"], "hf-" + p["job_id"])
        if p["tool"] == "generate_video":
            f = mk_video(tmp / f"{p['job_id']}.mp4", 2, size="720x1280", audio=False)
        else:
            f = mk_audio(tmp / f"{p['job_id']}.mp3", 7)
        hf.complete(db, cfg, p["job_id"], file=str(f))
    assert pipeline.assemble_ready(db, cfg) == 1
    final = Path(db.get_job(parent)["output"])
    info = ff.probe(final)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(ff.duration(final) - 7) < 0.6       # looped visuals to cover the narration
    posts = db.posts(status="awaiting_approval")
    assert {p["platform"] for p in posts} == {"youtube", "tiktok"}
    assert all(p["synthetic"] == 1 for p in posts)
    assert "AI-generated" in posts[0]["description"] and "Sources:" in posts[0]["description"]


def test_budget_guard(env):
    cfg, db, _ = env
    add_news(db, 5)  # 5 segments * 5 + 1.5 = 26.5 credits, budget 30
    assert pipeline.news_cycle(db, cfg)
    db.upsert_source({"id": "news:new", "platform": "news", "kind": "article", "url": "https://x/new",
                      "title": "GTA 6 more", "channel": "P", "published_at": 9e12, "meta": {"tier": "press"}})
    assert pipeline.news_cycle(db, cfg) is None  # second short would exceed the daily budget


def test_failed_segment_fails_parent(env):
    cfg, db, _ = env
    add_news(db, 2)
    parent = pipeline.news_cycle(db, cfg)
    first = hf.pending(db)[0]
    hf.complete(db, cfg, first["job_id"], error="nsfw filter")
    assert db.get_job(parent)["status"] == "failed"
    assert hf.ready(db) == []


def test_clip_job_licensed_twitch_clip(env, monkeypatch):
    cfg, db, tmp = env
    s = {"id": "twitch:clip:abc", "platform": "twitch", "kind": "clip", "url": "https://clips.twitch.tv/abc",
         "title": "GTA 6 cop chase goes insane #gta6", "channel": "streamer", "channel_id": "1",
         "published_at": 1e9, "views": 5000, "score": 5, "meta": {"game_match": True}}
    db.upsert_source(s)
    db.upsert_source({**s, "id": "twitch:clip:nope", "channel": "stranger", "score": 9})
    from gta6mm.clipping import download
    monkeypatch.setattr(download, "info", lambda url: {"duration": 20})

    def fake_fetch(url, out, sections=None, max_height=1080):
        Path(out).mkdir(parents=True, exist_ok=True)
        return [mk_video(Path(out) / "abc.mp4", 6)]

    monkeypatch.setattr(download, "fetch", fake_fetch)
    created = pipeline.plan_clips(db, cfg)
    assert len(created) == 1  # the unlicensed "stranger" clip is not planned
    assert pipeline.process_clips(db, cfg) == 1
    out = json.loads(db.get_job(created[0])["output"])[0]
    assert ff.duration(out) == pytest.approx(6, abs=0.3)
    post = db.posts(platform="youtube")[0]
    assert post["synthetic"] == 0 and "@streamer on Twitch" in post["description"]
    assert "permission" in post["description"]


def test_commentary_requires_voiceover(env, monkeypatch, tmp_path):
    cfg, db, tmp = env
    cfg["rights"]["allow_commentary"] = True
    db.upsert_source({"id": "youtube:v1", "platform": "youtube", "kind": "video", "url": "https://yt/v1",
                      "title": "GTA 6 trailer breakdown", "channel": "Other", "channel_id": "UCx",
                      "published_at": 1e9, "views": 1, "score": 3, "meta": {}})
    (jid,) = pipeline.plan_clips(db, cfg)
    assert db.get_job(jid)["status"] == "needs_voiceover"
    assert pipeline.process_clips(db, cfg) == 0
    from gta6mm.clipping import download
    monkeypatch.setattr(download, "info", lambda url: {"duration": 30})
    monkeypatch.setattr(download, "fetch", lambda url, out, sections=None, max_height=1080: [mk_video(tmp / "v1.mp4", 30)])
    pipeline.attach_voiceover(db, jid, str(mk_audio(tmp / "vo.wav", 8)))
    pipeline.process_clips(db, cfg)
    out = json.loads(db.get_job(jid)["output"])[0]
    # source capped at 12s; voiceover shorter, so output == capped excerpt
    assert ff.duration(out) == pytest.approx(12, abs=0.3)


def test_outreach_lists_blocked_creators(env):
    cfg, db, _ = env
    db.upsert_source({"id": "youtube:v9", "platform": "youtube", "kind": "video", "url": "https://yt/v9",
                      "title": "GTA 6 gameplay", "channel": "BigCreator", "published_at": 1e9, "views": 1, "score": 9})
    db.set_rights("youtube:v9", "blocked", "x")
    out = pipeline.outreach(db, cfg)
    assert out[0]["channel"] == "BigCreator" and "revenue share" in out[0]["message"]


def test_cancel_frees_budget(env):
    cfg, db, _ = env
    add_news(db, 5)
    parent = pipeline.news_cycle(db, cfg)
    assert db.credits_queued_since(0) > 0
    assert hf.cancel(db, parent) == 6
    assert db.credits_queued_since(0) == 0 and hf.pending(db) == []


def test_failed_assemble_is_retried_and_caption_errors_dont_block(env, monkeypatch):
    cfg, db, tmp = env
    cfg["clipping"]["captions"] = True
    add_news(db, 2)
    parent = pipeline.news_cycle(db, cfg)
    for p in hf.pending(db):
        f = (mk_video(tmp / f"{p['job_id']}.mp4", 2, size="720x1280", audio=False)
             if p["tool"] == "generate_video" else mk_audio(tmp / f"{p['job_id']}.mp3", 4))
        hf.complete(db, cfg, p["job_id"], file=str(f))
    db.update_job(parent, status="failed", error="old caption crash")  # state the user was left in
    from gta6mm.clipping import captions
    monkeypatch.setattr(captions, "transcribe", lambda *a, **k: None)  # captions unavailable
    assert pipeline.assemble_ready(db, cfg) == 1
    assert db.get_job(parent)["status"] == "done"
