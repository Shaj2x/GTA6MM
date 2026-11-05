"""YouTube Data API v3 discovery: fastest-growing GTA 6 uploads and live streams."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _get(path: str, key: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params={**params, "key": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def search(key: str, queries: list[str], lookback_hours: int, max_results: int) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: dict[str, str] = {}
    for q in queries:
        for event in (None, "live"):
            params = dict(part="id", q=q, type="video", maxResults=min(max_results, 50),
                          order="viewCount", relevanceLanguage="en")
            if event:
                params["eventType"] = event
            else:
                params["publishedAfter"] = after
            for item in _get("search", key, **params).get("items", []):
                ids[item["id"]["videoId"]] = event or "video"
    return details(key, list(ids))


def details(key: str, video_ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = _get("videos", key, part="snippet,statistics,status,liveStreamingDetails,contentDetails",
                    id=",".join(batch))
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            live = v.get("liveStreamingDetails", {})
            is_live = sn.get("liveBroadcastContent") == "live"
            out.append({
                "id": f"youtube:{v['id']}",
                "platform": "youtube",
                "kind": "stream" if is_live else "video",
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "title": sn.get("title"),
                "channel": sn.get("channelTitle"),
                "channel_id": sn.get("channelId"),
                "published_at": _ts(sn["publishedAt"]),
                "views": int(st.get("viewCount", 0)),
                "meta": {
                    "license": v.get("status", {}).get("license"),  # "youtube" | "creativeCommon"
                    "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)),
                    "viewer_count": int(live.get("concurrentViewers", 0) or 0),
                    "duration": v.get("contentDetails", {}).get("duration"),
                    "description": (sn.get("description") or "")[:500],
                },
            })
    return out


def stats(key: str, video_ids: list[str]) -> dict[str, dict]:
    """Current statistics for our own published videos (for the performance report)."""
    res = {}
    for i in range(0, len(video_ids), 50):
        data = _get("videos", key, part="statistics", id=",".join(video_ids[i:i + 50]))
        for v in data.get("items", []):
            res[v["id"]] = {k: int(x) for k, x in v.get("statistics", {}).items()}
    return res


def discover(cfg) -> list[dict]:
    key = cfg.env("YOUTUBE_API_KEY")
    if not key:
        return []
    d = cfg["discovery"]
    return search(key, d["queries"], d["lookback_hours"], d["max_results_per_query"])

