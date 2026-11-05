"""Twitch Helix discovery: top live GTA VI streams and the most-viewed clips.

Twitch clips are community-picked highlights, so they double as highlight
detection. Clip ownership stays with the broadcaster: only channels listed under
rights.licensed_creators are rendered; everyone else goes to the outreach list.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

HELIX = "https://api.twitch.tv/helix"


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class Twitch:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        r = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials"}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def get(self, path: str, **params) -> dict:
        r = requests.get(f"{HELIX}/{path}", params=params, timeout=30, headers={
            "Client-ID": self.client_id, "Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()

    def game_id(self, name: str) -> str | None:
        data = self.get("games", name=name).get("data", [])
        return data[0]["id"] if data else None

    def streams(self, game_id: str, first: int = 25) -> list[dict]:
        out = []
        for s in self.get("streams", game_id=game_id, first=first).get("data", []):
            out.append({
                "id": f"twitch:stream:{s['user_login']}",
                "platform": "twitch", "kind": "stream",
                "url": f"https://www.twitch.tv/{s['user_login']}",
                "title": s.get("title"), "channel": s["user_login"], "channel_id": s["user_id"],
                "published_at": _ts(s["started_at"]), "views": s.get("viewer_count", 0),
                "meta": {"viewer_count": s.get("viewer_count", 0), "game_match": True,
                         "language": s.get("language")},
            })
        return out

    def clips(self, game_id: str, hours: int, first: int = 50) -> list[dict]:
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for c in self.get("clips", game_id=game_id, started_at=start, first=first).get("data", []):
            out.append({
                "id": f"twitch:clip:{c['id']}",
                "platform": "twitch", "kind": "clip", "url": c["url"],
                "title": c.get("title"), "channel": c["broadcaster_name"].lower(),
                "channel_id": c["broadcaster_id"], "published_at": _ts(c["created_at"]),
                "views": c.get("view_count", 0),
                "meta": {"game_match": True, "duration": c.get("duration"),
                         "creator": c.get("creator_name"), "vod_offset": c.get("vod_offset")},
            })
        return out


def discover(cfg) -> list[dict]:
    cid, secret = cfg.env("TWITCH_CLIENT_ID"), cfg.env("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        return []
    tw = Twitch(cid, secret)
    gid = tw.game_id(cfg["discovery"]["twitch_game_name"])
    if not gid:
        return []
    return tw.streams(gid) + tw.clips(gid, cfg["discovery"]["lookback_hours"])
