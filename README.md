# GTA6MM: GTA 6 launch content engine

An automated pipeline for the GTA VI launch window (console release **Nov 19, 2026**):

1. **Discovers** what's blowing up right now: YouTube uploads and live streams ranked by
   views-per-hour, top Twitch streams and clips, and news from Rockstar, gaming press and Reddit.
2. **Clips** trending videos and streams into vertical 9:16 Shorts/TikToks. It finds highlights
   from YouTube "Most replayed" data, Twitch clip views, or audio-energy peaks, then adds a hook
   title, burned captions and a creator credit. It only clips sources you have rights to (see below).
3. **Builds an official media library.** It downloads Rockstar's official GTA VI trailers from
   their YouTube channel (video only), splits them into shots, and has Claude describe each shot.
   You can add official screenshots by dropping them into `data/library/images/`.
4. **Makes fast 15–25s news Shorts.** Claude writes a hook plus 3–5 beats from the news, citing a
   source for every claim and tagging leaks as RUMOR, and picks the matching real shots. The editor
   cuts every 1–2s with full-screen pans, flashes on each beat, big 1–3 word captions and the hook
   on screen. Higgsfield makes the narration (Gideon voice), plus at most one AI effect shot.
   Leaked footage is never used.
5. **Publishes after you approve.** YouTube uploads use the synthetic-media disclosure flag.
   TikTok posts go through Higgsfield's TikTok connector with the AI-generated flag set.

```
discover ─► score ─► rights gate ─┬─► clip (yt-dlp + ffmpeg) ─────────────┐
                                  └─► outreach DMs for blocked creators     │
news ─► Claude script (cited) ─► Higgsfield queue ─► agent renders ─► assemble ─► review ─► publish
```

## Setup

```bash
pip install -e '.[captions,dev]'      # needs ffmpeg on PATH
```

```bash
cp config.example.yaml config.yaml
```

```bash
cp .env.example .env                  # fill in the keys you have
```

| Key | Unlocks |
|---|---|
| `YOUTUBE_API_KEY` | YouTube trend discovery and `stats` |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | Twitch live streams and clips |
| `ANTHROPIC_API_KEY` | Claude-written scripts (a plain headline template is used without it) |
| `YOUTUBE_OAUTH_*` | Automatic YouTube upload |
| Higgsfield connector | AI video and narration, plus TikTok posting (driven by Claude Code, see below) |

Run the tests: `pytest -q`.

## Daily operation

```bash
gta6mm run --every 30          # discover → clip → news script → queue AI → assemble → publish, every 30 min
```

Then, in Claude Code inside this repo:

```
/higgsfield-render
```

This renders the queued AI segments through the Higgsfield connector, downloads the results and
assembles the finished shorts. To schedule it, ask Claude to run `/higgsfield-render` every few hours.

Review and approve:

```bash
gta6mm review                  # posts awaiting approval (file path, title)
gta6mm approve post-ab12 post-cd34
gta6mm publish                 # YouTube upload + TikTok queue (TikTok: ask Claude to "publish the TikTok queue")
```

Other commands: `trending`, `outreach`, `status`, `stats`, `news --force`,
`clip-voiceover <job> my_take.wav`, `hf-pending`, `hf-complete`.

## Clipping and rights (read this)

Reposting other people's streams without permission is how clip channels get copyright strikes,
Content ID claims that take all the revenue, and YouTube Partner Program rejections for
"reused content". That would wipe out the profit, so the clipper is **closed by default**:

| Mode | When | What happens |
|---|---|---|
| `owned` | Your channel (`rights.own_channels`) | Full clips |
| `licensed` | Creator agreed in writing (`rights.licensed_creators`, `evidence` required) | Full clips with credit |
| `cc_by` | The YouTube video is Creative Commons licensed | Full clips with credit |
| `commentary` | Opt-in (`allow_commentary: true`) | Excerpt capped at 12s; won't render without **your** voiceover |
| `blocked` | Everything else | Never rendered; added to `data/outreach.json` |

**How to get the most out of this:** run `gta6mm outreach` daily. It lists the creators who are
trending right now, each with a ready-to-send DM asking for clip permission (with an optional
revenue share). Many streamers want clip channels because clips bring them viewers. Each "yes"
goes into `licensed_creators`, and from then on their content is clipped automatically.
Commentary mode is a fair-use judgement call, not a guarantee. Use it for real reaction or
breakdown content with your own voice.

## Using Rockstar footage

Rockstar's [video policy](https://support.rockstargames.com/articles/200153756/rockstar-games-video-policy)
covers only **non-commercial** fan use, and it removes isolated cinematics. Monetized Shorts that
use trailer footage therefore rely on fair use for news commentary, not on Rockstar's permission.
The editor keeps each video commentary: short reframed shots under your narration, never a
re-upload. **Trailer audio is always muted**, because the licensed songs trigger Content ID claims
that send your ad revenue to the label. Leaked footage is never downloaded or shown.

```bash
gta6mm library sync      # download official trailers, cut into shots, tag them
gta6mm library stats
```

## News Shorts: what keeps them monetizable

- **Facts come from sources.** Scripts can only state what the fetched news items support.
  Leaks and Reddit claims are shown on screen as `RUMOR / UNCONFIRMED`, and every description
  lists its sources. Presenting AI footage as "leaked gameplay" gets videos removed, and
  viewers won't trust the channel again after launch day.
- **The visuals are original.** Prompts are cleaned so they never ask for Rockstar logos, the
  HUD, or named characters such as Lucia or Jason. Instead they describe the setting (a sunny,
  neon-lit Miami-style city). The narration can still name GTA 6, because that's news commentary.
- **The AI content is disclosed.** There's an on-screen badge, YouTube's
  `containsSyntheticMedia=true`, TikTok's `is_aigc`, and a description line saying the channel
  isn't affiliated with Rockstar.

## Costs (Higgsfield, checked 2026-11-05)

| Item | Credits |
|---|---|
| Seedance 2.0 Mini, 5s 720p 9:16 (default) | 5 |
| Kling 3.0, 5s std | 7.5 |
| seed_audio narration | ~1.2 |
| **One 5-segment news short** | **~26** |

`higgsfield.daily_credit_budget` (default 60) is enforced when jobs are queued.
The `/higgsfield-render` command checks your balance before it submits anything.

## Launch day: clipping streamers, YouTubers and TikTokers

From `release_date` on, **launch mode switches on automatically**: wider gameplay searches every
cycle, 4 clips per source, up to 12 new clip jobs per cycle. Clip any specific link with:

```bash
gta6mm clip-url https://www.twitch.tv/videos/123 https://www.tiktok.com/@creator/video/456
```

YouTube videos and Shorts, Twitch VODs and clips, and TikTok links all work. Creators you have
permission from (`rights.licensed_creators`, with `platform: tiktok` for TikTokers) are clipped
straight away. Anyone else gets a ready-to-send permission message instead. TikTok has no public
search API, so TikTok clips come from links you paste (or creators who send you theirs).

## Launch-window playbook

| When | What to post |
|---|---|
| Now → Nov 18 | 1–2 AI news shorts a day (countdown, confirmed features, the map and characters as officially shown); daily outreach |
| Nov 18–19 (launch) | Clip licensed streamers' first hours hard. Run every 15 min with `clips_per_source: 4` |
| Launch week | Highlight compilations from licensed creators, plus "things you missed" news shorts from patch notes and the Newswire |

## Layout

```
gta6mm/discovery/   youtube.py twitch.py news.py scoring.py
gta6mm/rights.py    rights gate + outreach messages
gta6mm/clipping/    download.py (yt-dlp) highlights.py edit.py (ffmpeg 9:16 renderer) captions.py
gta6mm/scripting/   writer.py (Claude structured output + citation validator)
gta6mm/higgsfield/  jobs.py (queue/budget/complete) prompts.py (IP-safe prompts)
gta6mm/publish/     metadata.py youtube_upload.py
gta6mm/pipeline.py  orchestration      gta6mm/cli.py  commands
.claude/commands/higgsfield-render.md  agent runbook for the Higgsfield connector
```
