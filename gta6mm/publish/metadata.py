"""Titles, descriptions, tags and disclosures for posts."""
from __future__ import annotations

from datetime import date

AI_NOTE = ("Footage from official Rockstar Games trailers and screenshots, used for news commentary. "
           "Not affiliated with or endorsed by Rockstar Games or Take-Two Interactive.")


def countdown(release: str, today: date | None = None) -> str:
    d = (date.fromisoformat(release) - (today or date.today())).days
    if d > 1:
        return f"{d} days until GTA 6"
    if d == 1:
        return "GTA 6 launches tomorrow"
    if d == 0:
        return "GTA 6 is out today"
    return f"GTA 6 day {-d + 1}"


def hashtags(tags: list[str], n: int = 5) -> str:
    seen, out = set(), []
    for t in tags:
        h = "#" + "".join(ch for ch in t.title() if ch.isalnum())
        if h.lower() not in seen and len(h) > 2:
            seen.add(h.lower())
            out.append(h)
    return " ".join(out[:n])


def for_news(script: dict, cfg) -> dict:
    tags = list(dict.fromkeys(script.get("tags", []) + cfg["publish"]["default_tags"]))[:15]
    lines = [script.get("description") or script.get("hook", ""), "",
             countdown(cfg["release_date"]), ""]
    if script.get("has_rumor"):
        lines += ["Segments marked RUMOR are unconfirmed reports.", ""]
    lines += ["Sources:"] + [f"- {s['title']} ({s['tier']}): {s['url']}" for s in script.get("sources", [])]
    lines += ["", AI_NOTE, "", hashtags(tags)]
    return {"title": script["title"][:95], "description": "\n".join(lines)[:4900], "tags": tags,
            "synthetic": True}


def for_clip(src: dict, decision, cfg, hook: str) -> dict:
    platform = {"twitch": "Twitch", "youtube": "YouTube", "tiktok": "TikTok", "kick": "Kick"}.get(src["platform"], src["platform"])
    tags = cfg["publish"]["default_tags"]
    lines = [hook, "", f"Original: {decision.attribution or src.get('channel')} on {platform}: {src['url']}"]
    if decision.mode == "licensed":
        lines.append("Used with the creator's permission.")
    elif decision.mode == "cc_by":
        lines.append("Licensed under Creative Commons Attribution (CC BY).")
    elif decision.mode == "commentary":
        lines.append("Short excerpt used for commentary and criticism.")
    lines += ["", countdown(cfg["release_date"]), "", hashtags(tags)]
    return {"title": hook[:95], "description": "\n".join(lines), "tags": tags, "synthetic": False}
