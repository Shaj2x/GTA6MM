"""News feed ingestion with source tiers (official > press > community)."""
from __future__ import annotations

import calendar
import hashlib
import re
import time

import feedparser

TIER_RANK = {"official": 3, "press": 2, "community": 1}


def parse_feed(raw: str | bytes, url: str, tier: str, pattern: str | None = None) -> list[dict]:
    feed = feedparser.parse(raw)
    rx = re.compile(pattern, re.I) if pattern else None
    out = []
    for e in feed.entries:
        title = e.get("title", "")
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        if rx and not (rx.search(title) or rx.search(summary)):
            continue
        link = e.get("link") or url
        tm = e.get("published_parsed") or e.get("updated_parsed")
        published = calendar.timegm(tm) if tm else time.time()
        out.append({
            "id": "news:" + hashlib.sha1(link.encode()).hexdigest()[:16],
            "platform": "news", "kind": "article", "url": link, "title": title,
            "channel": feed.feed.get("title", url), "published_at": published, "views": 0,
            "meta": {"tier": tier, "summary": summary[:1200], "feed": url, "game_match": True},
        })
    return out


def discover(cfg) -> list[dict]:
    import requests

    items = []
    for f in cfg["discovery"].get("news_feeds", []):
        try:
            r = requests.get(f["url"], timeout=30, headers={"User-Agent": "gta6mm-newsreader/0.1"})
            r.raise_for_status()
            items += parse_feed(r.content, f["url"], f.get("tier", "community"), f.get("filter"))
        except Exception as exc:  # one dead feed must not stop the cycle
            print(f"[news] {f['url']}: {exc}")
    return items
