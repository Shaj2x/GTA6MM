"""Trend scoring: rank sources by how fast they are growing, not just total views."""
from __future__ import annotations

import math
import re
import time

# Topics that historically spike around a GTA drop. Weighted multiplicatively.
BOOST_TERMS = {
    r"\btrailer\b": 1.35,
    r"\bgameplay\b": 1.3,
    r"\bmap\b": 1.25,
    r"\bleak(ed|s)?\b": 1.2,
    r"\brelease\b|\blaunch\b|\bdelay": 1.25,
    r"\bonline\b": 1.15,
    r"\bpre-?order|\bprice\b": 1.15,
    r"\bfirst (look|hour|mission)\b": 1.3,
    r"\bsecret|easter egg|hidden\b": 1.2,
    r"\bfunny|fail|insane|crazy\b": 1.1,
}
GTA_RE = re.compile(r"\bgta\s*(6|vi)\b|grand theft auto\s*(6|vi)\b", re.I)


def velocity(views: int, published_at: float | None, now: float | None = None) -> float:
    """Views per hour since publish (min 0.5h to avoid div-by-tiny)."""
    now = now or time.time()
    if not published_at:
        return 0.0
    hours = max((now - published_at) / 3600.0, 0.5)
    return views / hours


def score(source: dict, now: float | None = None) -> float:
    """Composite score: log(velocity) dominates, total views and live viewers add,
    recency decays, topical keywords boost. Off-topic items score 0."""
    now = now or time.time()
    title = source.get("title") or ""
    if not GTA_RE.search(title) and not source.get("meta", {}).get("game_match"):
        return 0.0
    views = int(source.get("views") or 0)
    meta = source.get("meta") or {}
    if source.get("kind") == "stream":
        # live viewers are worth a lot more than cumulative views
        base = math.log10(meta.get("viewer_count", views) + 1) * 3.0
    else:
        v = velocity(views, source.get("published_at"), now)
        base = math.log10(v + 1) * 2.0 + math.log10(views + 1) * 0.5
    age_h = max((now - (source.get("published_at") or now)) / 3600.0, 0.0)
    decay = 0.5 ** (age_h / 24.0)  # half-life one day
    mult = 1.0
    for pat, w in BOOST_TERMS.items():
        if re.search(pat, title, re.I):
            mult *= w
    return round(base * (0.4 + 0.6 * decay) * mult, 4)
