import time

from gta6mm import rights
from gta6mm.clipping import captions, highlights
from gta6mm.discovery import news, scoring
from gta6mm.higgsfield import prompts
from gta6mm.publish import metadata, youtube_upload
from gta6mm.scripting import writer

NOW = time.time()


def src(**kw):
    base = {"id": "youtube:x", "platform": "youtube", "kind": "video", "url": "u", "title": "GTA 6 map leak",
            "channel": "Creator", "channel_id": "UC1", "published_at": NOW - 3600, "views": 100000, "meta": {}}
    base.update(kw)
    return base


# ---------------------------------------------------------------- rights
def test_rights_default_blocked():
    d = rights.evaluate(src(), {})
    assert d.mode == "blocked" and not d.allowed


def test_rights_owned_licensed_cc():
    cfg = {"own_channels": [{"platform": "youtube", "channel_id": "UC1"}]}
    assert rights.evaluate(src(), cfg).mode == "owned"
    cfg = {"licensed_creators": [{"platform": "twitch", "channel": "Streamer", "evidence": "DM"}]}
    d = rights.evaluate(src(platform="twitch", channel="streamer"), cfg)
    assert d.mode == "licensed" and d.allowed
    assert rights.evaluate(src(meta={"license": "creativeCommon"}), {}).mode == "cc_by"


def test_rights_licensed_needs_evidence_and_blocklist_wins():
    cfg = {"licensed_creators": [{"channel": "creator"}]}
    assert rights.evaluate(src(), cfg).mode == "blocked"
    cfg = {"own_channels": [{"channel_id": "UC1"}], "block_channels": ["creator"]}
    assert rights.evaluate(src(), cfg).mode == "blocked"


def test_rights_commentary_constraints():
    d = rights.evaluate(src(), {"allow_commentary": True, "commentary": {"max_source_seconds": 10}})
    assert d.mode == "commentary"
    assert d.constraints == {"max_source_seconds": 10.0, "require_voiceover": True}


# ---------------------------------------------------------------- scoring
def test_score_prefers_velocity_and_ignores_offtopic():
    fast = scoring.score(src(views=200000, published_at=NOW - 2 * 3600), NOW)
    slow = scoring.score(src(views=200000, published_at=NOW - 40 * 3600), NOW)
    assert fast > slow > 0
    assert scoring.score(src(title="Minecraft speedrun"), NOW) == 0


# ---------------------------------------------------------------- highlights
def test_pick_windows_non_overlapping():
    series = [(t, 1.0) for t in range(100)] + [(50, 9.0), (52, 8.0), (80, 7.0)]
    w = highlights.pick_windows(series, 10, 3, total=100)
    assert len(w) == 3
    for (a, b), (c, d) in zip(w, w[1:]):
        assert b <= c
    assert any(a <= 50 <= b for a, b in w) and any(a <= 80 <= b for a, b in w)


def test_heatmap_skips_intro():
    hm = [{"start_time": i, "end_time": i + 1, "value": 1.0 if i else 10.0} for i in range(100)]
    hm[60]["value"] = 5.0
    (a, b), = highlights.from_heatmap(hm, 10, 1, 100)
    assert a <= 60.5 <= b


def test_caption_chunks():
    words = [(0, .3, "Rockstar"), (.3, .6, "just"), (.6, .9, "confirmed"), (.9, 1.2, "it."), (1.2, 1.5, "Wow")]
    chunks = captions.chunk_words(words)
    assert chunks[0][2] == "ROCKSTAR JUST CONFIRMED IT." and chunks[1][2] == "WOW"
    assert "00:00:00,000 --> 00:00:01,200" in captions.to_srt(chunks)


# ---------------------------------------------------------------- news + scripts
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Press</title>
<item><title>GTA 6 trailer 3 date confirmed</title><link>https://ex.com/a</link>
<description>&lt;p&gt;Rockstar says...&lt;/p&gt;</description><pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Best pizza in town</title><link>https://ex.com/b</link></item>
</channel></rss>"""


def test_parse_feed_filters_and_tiers():
    items = news.parse_feed(RSS, "https://ex.com/rss", "press", "GTA")
    assert len(items) == 1
    assert items[0]["meta"]["tier"] == "press" and items[0]["meta"]["summary"] == "Rockstar says..."


def _items():
    return [
        {"id": "n1", "title": "Release date holds", "url": "https://o", "channel": "Rockstar", "meta": {"tier": "official"}},
        {"id": "n2", "title": "Map size report", "url": "https://p", "channel": "IGN", "meta": {"tier": "press"}},
        {"id": "n3", "title": "Leaked car list", "url": "https://r", "channel": "reddit", "meta": {"tier": "community"}},
    ]


SHOTS = [{"id": f"t#{i}"} for i in range(6)]


def test_validate_sets_status_drops_unsourced_and_caps_ai():
    script = {"title": "t", "hook": "h", "hook_text": "H", "description": "", "tags": [], "beats": [
        {"narration": "a", "shot_ids": ["t#0", "nope"], "ai_effect_prompt": None, "claim_ids": ["n1"], "is_factual": True},
        {"narration": "b", "shot_ids": ["t#0", "t#1"], "ai_effect_prompt": "neon map", "claim_ids": ["n1", "n3"], "is_factual": True},
        {"narration": "c", "shot_ids": [], "ai_effect_prompt": None, "claim_ids": ["bogus"], "is_factual": True},
        {"narration": "d", "shot_ids": ["t#2"], "ai_effect_prompt": "second ai", "claim_ids": [], "is_factual": False},
    ]}
    out = writer.validate(script, _items(), SHOTS, max_ai_clips=1)
    assert [b["status"] for b in out["beats"]] == ["confirmed", "rumor", "opinion"]
    assert out["beats"][0]["shot_ids"] == ["t#0"]            # unknown id dropped
    assert out["beats"][1]["shot_ids"] == ["t#1"]            # no shot reused within a video
    assert out["beats"][1]["badge"] == "RUMOR"
    assert out["beats"][2]["ai_effect_prompt"] is None       # only one AI effect allowed
    assert out["has_rumor"] and {s["id"] for s in out["sources"]} == {"n1", "n3"}


def test_template_fallback_validates():
    out = writer.validate(writer.template(_items(), SHOTS), _items(), SHOTS)
    assert out["beats"][0]["status"] == "confirmed"
    assert all(b["ai_effect_prompt"] is None for b in out["beats"])


def test_split_shots_and_section_times():
    from gta6mm.clipping import timeline
    from gta6mm.library import media
    shots = media.split_shots([1.0, 1.3, 9.0], 10.0, 0.7, 2.5)
    assert shots[0] == (0.0, 1.0) and (1.0, 1.3) not in shots       # too-short shot dropped
    assert shots[1:4] == [(1.3, 3.8), (3.8, 6.3), (6.3, 8.8)]          # long shot capped at 3 windows
    secs = ["hook words here", "beat one two", "beat three"]
    t = timeline.section_times(secs, None, 8.0)
    assert t[0] == (0.0, 3.0) and t[2][1] == 8.0
    ws = [(i * 1.0, i * 1.0 + .9, "w") for i in range(8)]
    t2 = timeline.section_times(secs, ws, 8.0)
    assert t2[1][0] == 3.0


# ---------------------------------------------------------------- prompts + metadata
def test_sanitize_strips_ip():
    p = prompts.sanitize("Lucia and Jason drive through Vice City in GTA 6 with the HUD and Rockstar logo")
    assert not prompts.flagged(p)
    assert "Miami-style" in p and "no logos" in p


def test_countdown():
    from datetime import date
    assert metadata.countdown("2026-11-19", date(2026, 11, 5)) == "14 days until GTA 6"
    assert metadata.countdown("2026-11-19", date(2026, 11, 19)) == "GTA 6 is out today"
    assert metadata.countdown("2026-11-19", date(2026, 11, 21)) == "GTA 6 day 3"


def test_youtube_body_discloses_synthetic():
    b = youtube_upload.body({"title": "t", "description": "d", "tags": [], "synthetic": 1}, "private")
    assert b["status"]["containsSyntheticMedia"] is True and b["status"]["privacyStatus"] == "private"


def test_resolve_font_falls_back_when_configured_font_missing():
    from pathlib import Path
    from gta6mm.clipping import ffmpeg as ff
    f = ff.resolve_font("/nope/DoesNotExist.ttf")
    assert Path(f).is_file()


def test_render_uses_only_core_ffmpeg_filters(tmp_path):
    """Homebrew's ffmpeg has no drawtext/subtitles; text must be PNG overlays."""
    import subprocess
    from pathlib import Path
    from gta6mm.clipping import edit
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:duration=2",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n")
    spec = edit.RenderSpec(src=src, out=tmp_path / "o.mp4", font="", title="t", badge="b", credit="c",
                           captions_srt=srt, overlays=[edit.Overlay("x", 0, 1)])
    spec.font = __import__("gta6mm.clipping.ffmpeg", fromlist=["x"]).resolve_font("")
    cmd = " ".join(edit.build_command(spec, tmp_path))
    assert "drawtext" not in cmd and "subtitles=" not in cmd
    assert edit.parse_srt(srt) == [(0.0, 1.0, "HELLO")]
    edit.render(spec)
    assert Path(spec.out).exists()
