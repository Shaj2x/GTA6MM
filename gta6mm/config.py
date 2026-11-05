"""Configuration: YAML file merged over defaults, plus secrets from env / .env."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "release_date": "2026-11-19",
    "discovery": {
        "queries": ["GTA 6", "GTA VI", "Grand Theft Auto VI"],
        "lookback_hours": 48,
        "max_results_per_query": 25,
        "twitch_game_name": "Grand Theft Auto VI",
        "news_feeds": [
            {"url": "https://www.rockstargames.com/newswire/rss", "tier": "official"},
            {"url": "https://www.ign.com/rss/articles/feed", "tier": "press", "filter": "GTA|Grand Theft Auto"},
            {"url": "https://www.gamespot.com/feeds/news/", "tier": "press", "filter": "GTA|Grand Theft Auto"},
            {"url": "https://www.pcgamer.com/rss/", "tier": "press", "filter": "GTA|Grand Theft Auto"},
            {"url": "https://www.reddit.com/r/GTA6/top/.rss?t=day", "tier": "community"},
        ],
    },
    "rights": {
        "own_channels": [],
        "licensed_creators": [],
        "allow_commentary": False,
        "commentary": {"max_source_seconds": 12, "require_voiceover": True},
        "block_channels": [],
    },
    "clipping": {
        "clips_per_source": 3,
        "clip_seconds": 30,
        "max_new_clip_jobs_per_cycle": 6,
        "font": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "captions": True,
    },
    "scripting": {"model": "claude-opus-5-5", "effort": "medium", "max_items": 12},
    "higgsfield": {
        "video_model": "seedance_2_0_mini",
        "video_params": {"duration": 5, "resolution": "720p", "generate_audio": False},
        "voice_model": "seed_audio",
        "voice_id": "",           # from Higgsfield list_voices; empty = agent picks a preset
        "aspect_ratio": "9:16",
        "daily_credit_budget": 60,
        "est_credits_per_video": 5,
        "est_credits_per_voiceover": 1.5,
    },
    "publish": {
        "auto_approve": False,
        "youtube_privacy": "private",
        "channel_handle": "",
        "default_tags": ["GTA 6", "GTA VI", "Grand Theft Auto VI", "gaming news"],
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Config(dict):
    @property
    def data_dir(self) -> Path:
        p = Path(self["data_dir"])
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def env(name: str) -> str | None:
        return os.environ.get(name) or None


def load(path: str | os.PathLike | None = None) -> Config:
    load_dotenv(Path(".env"))
    path = Path(path or os.environ.get("GTA6MM_CONFIG", "config.yaml"))
    user = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Config(_merge(DEFAULTS, user or {}))
