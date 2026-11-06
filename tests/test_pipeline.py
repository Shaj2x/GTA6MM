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


def mk_trailer(path: Path):
    """Fake 'official trailer': 4 visually distinct 2s shots -> 3 scene cuts."""
    srcs = ["testsrc2", "smptebars", "testsrc", "rgbtestsrc"]
    args = ["ffmpeg", "-v", "error", "-y"]
    for s in srcs:
        args += ["-f", "lavfi", "-i", f"{s}=size=1920x1080:rate=30:duration=2"]
    fc = "".join(f"[{i}:v]format=yuv420p,setsar=1[v{i}];" for i in range(4))
    fc += "".join(f"[v{i}]" for i in range(4)) + "concat=n=4:v=1:a=0[v]"
    subprocess.run(args + ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", str(path)], check=True)
    return path


@pytest.fixture
def lib(env):
    from gta6mm.library import media
    cfg, db, tmp = env
    trailer = mk_trailer(media.lib_dir(cfg) / "videos" / "trailer2.mp4")
    n = media.index_video(db, cfg, trailer, "trailer2", "Grand Theft Auto VI Trailer 2")
    assert n >= 4
    return env


def complete_all(db, cfg, tmp, voice_seconds=8):
    for p in hf.pending(db):
        f = (mk_video(tmp / f"{p['job_id']}.mp4", 5, size="720x1280", audio=False)
             if p["tool"] == "generate_video" else mk_audio(tmp / f"{p['job_id']}.mp3", voice_seconds))
        hf.complete(db, cfg, p["job_id"], file=str(f))
    pipeline.assemble_ready(db, cfg)   # what `gta6mm hf-complete` runs


def test_news_short_uses_real_footage_and_is_short(lib):
    cfg, db, tmp = lib
    add_news(db, 3)
    parent = pipeline.news_cycle(db, cfg)
    assert parent
    pend = hf.pending(db)
    assert [p["tool"] for p in pend] == ["generate_audio"]           # no AI video by default
    assert pend[0]["params"]["speech_rate"] == 30
    assert pipeline.news_cycle(db, cfg) is None                       # same news -> no duplicate
    complete_all(db, cfg, tmp, voice_seconds=8)
    final = Path(db.get_job(parent)["output"])
    v = next(s for s in ff.probe(final)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(ff.duration(final) - 8.35) < 0.5                       # length follows the narration
    posts = db.posts(status="awaiting_approval")
    assert {p["platform"] for p in posts} == {"youtube", "tiktok"}
    assert all(p["synthetic"] == 0 for p in posts)
    assert "official Rockstar Games trailers" in posts[0]["description"]
    assert sum(s["used_count"] for s in db.shots()) >= 5              # fast cuts over many shots


def test_one_ai_effect_cut_is_flagged(lib):
    from gta6mm.scripting import writer
    cfg, db, tmp = lib
    add_news(db, 2)
    items = db.news(0)
    shots = db.shots()
    script = writer.validate({"title": "t", "hook": "Rockstar just changed everything", "hook_text": "BIG NEWS",
        "description": "", "tags": [], "beats": [
            {"narration": "The swamps are huge and full of gators", "shot_ids": [shots[0]["id"]],
             "ai_effect_prompt": "neon map of a coastal state zooming in", "claim_ids": [items[0]["id"]], "is_factual": True},
            {"narration": "And it all lands in thirteen days", "shot_ids": [shots[1]["id"]],
             "ai_effect_prompt": None, "claim_ids": [items[1]["id"]], "is_factual": True}]}, items, shots)
    parent = hf.queue_script(db, cfg, script)
    assert sorted(p["tool"] for p in hf.pending(db)) == ["generate_audio", "generate_video"]
    complete_all(db, cfg, tmp, voice_seconds=6)
    assert db.get_job(parent)["status"] == "done"
    assert all(p["synthetic"] == 1 for p in db.posts())


def test_empty_library_blocks_news(env):
    cfg, db, _ = env
    add_news(db, 3)
    assert pipeline.news_cycle(db, cfg) is None


def test_budget_guard(lib):
    cfg, db, _ = lib
    cfg["higgsfield"]["daily_credit_budget"] = 2
    add_news(db, 3)
    assert pipeline.news_cycle(db, cfg)               # 1.5 credits of narration fits
    db.upsert_source({"id": "news:new", "platform": "news", "kind": "article", "url": "https://x/new",
                      "title": "GTA 6 more", "channel": "P", "published_at": 9e12, "meta": {"tier": "press"}})
    assert pipeline.news_cycle(db, cfg) is None        # a second one would exceed the budget


def test_failed_segment_fails_parent(lib):
    cfg, db, _ = lib
    add_news(db, 2)
    parent = pipeline.news_cycle(db, cfg)
    hf.complete(db, cfg, hf.pending(db)[0]["job_id"], error="provider error")
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


def test_cancel_frees_budget(lib):
    cfg, db, _ = lib
    add_news(db, 5)
    parent = pipeline.news_cycle(db, cfg)
    assert db.credits_queued_since(0) > 0
    assert hf.cancel(db, parent) == 1
    assert db.credits_queued_since(0) == 0 and hf.pending(db) == []


def test_failed_assemble_is_retried_and_caption_errors_dont_block(lib, monkeypatch):
    cfg, db, tmp = lib
    add_news(db, 2)
    parent = pipeline.news_cycle(db, cfg)
    db.update_job(parent, status="failed", error="earlier crash")
    from gta6mm.clipping import captions
    monkeypatch.setattr(captions, "words", lambda *a, **k: None)   # whisper unavailable
    complete_all(db, cfg, tmp, voice_seconds=4)
    assert db.get_job(parent)["status"] == "done"


def test_clip_url_tiktok_licensed_and_blocked(env, monkeypatch):
    cfg, db, tmp = env
    cfg["rights"]["licensed_creators"].append(
        {"platform": "tiktok", "channel": "gtaclipper", "evidence": "TikTok DM 11/06", "attribution": "@gtaclipper"})
    from gta6mm.clipping import download
    infos = {
        "https://www.tiktok.com/@gtaclipper/video/1": {"extractor_key": "TikTok", "id": "1", "uploader_id": "gtaclipper",
            "title": "GTA 6 cops chase me into the swamp", "duration": 9, "view_count": 50000},
        "https://www.tiktok.com/@stranger/video/2": {"extractor_key": "TikTok", "id": "2", "uploader_id": "stranger",
            "title": "GTA 6 insane stunt", "duration": 12},
    }
    monkeypatch.setattr(download, "info", lambda url: infos[url])

    def fake_fetch(url, out, sections=None, max_height=1080):
        Path(out).mkdir(parents=True, exist_ok=True)
        return [mk_video(Path(out) / "v.mp4", 9, size="720x1280")]   # TikToks are vertical
    monkeypatch.setattr(download, "fetch", fake_fetch)

    ok = pipeline.add_url(db, cfg, "https://www.tiktok.com/@gtaclipper/video/1")
    assert ok["platform"] == "tiktok" and ok["rights"] == "licensed" and ok["job"].startswith("clip-")
    no = pipeline.add_url(db, cfg, "https://www.tiktok.com/@stranger/video/2")
    assert no["rights"] == "blocked" and "revenue share" in no["ask_permission_message"]
    assert pipeline.process_clips(db, cfg) == 1
    post = db.posts(platform="youtube")[0]
    assert "@gtaclipper on TikTok" in post["description"]


def test_launch_mode_switches_on_release_day(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("release_date: '2000-01-01'\n")
    cfg = config.load()
    assert cfg.get("launch_mode") and cfg["clipping"]["clips_per_source"] == 4
    (tmp_path / "config.yaml").write_text(f"release_date: '{date.today().replace(year=date.today().year + 1)}'\n")
    assert not config.load().get("launch_mode")
