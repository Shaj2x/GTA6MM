"""News items -> a sourced 30-45s short-form script.

Every factual line must cite the ids of the news items that support it. The
validator then sets each segment's status from the *weakest* source it cites:
official -> CONFIRMED, press -> REPORTED, community -> RUMOR. Uncited factual
claims are dropped, so the channel never states a leak or guess as fact.
"""
from __future__ import annotations

import json
from typing import Any

from ..discovery.news import TIER_RANK

STATUS_BY_TIER = {"official": "confirmed", "press": "reported", "community": "rumor"}
BADGE = {"confirmed": "", "reported": "REPORTED", "rumor": "RUMOR / UNCONFIRMED"}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook", "segments", "description", "tags"],
    "properties": {
        "title": {"type": "string", "description": "<=70 chars, curiosity-driven, no clickbait lies"},
        "hook": {"type": "string", "description": "first 2 seconds of narration"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "on_screen_text", "visual_prompt", "claim_ids", "is_factual"],
                "properties": {
                    "narration": {"type": "string"},
                    "on_screen_text": {"type": "string", "description": "<=6 words"},
                    "visual_prompt": {"type": "string",
                                      "description": "original cinematic scene, no game logos/characters"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                    "is_factual": {"type": "boolean",
                                   "description": "true if narration asserts something about the game"},
                },
            },
        },
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You write scripts for a GTA 6 news Shorts/TikTok channel ahead of the game's launch.

Rules:
- Use ONLY the news items provided. Every segment that states anything about the game
  (features, map, dates, price, characters, mechanics) sets is_factual=true and lists
  the ids of the items that support it in claim_ids. Never invent details.
- Items marked community are rumours: phrase them as "reportedly", "rumour says",
  "fans spotted" - never as fact.
- 4-6 segments. Each segment plays over one 5-second AI clip, so keep each
  narration to 10-14 words. Hook (<=10 words) comes first.
- visual_prompt describes an ORIGINAL cinematic scene for an AI video model: a sunny,
  neon-lit, Miami-style coastal city, swamps, highways, beaches, sports cars, boats,
  nightlife. Do NOT mention GTA, Rockstar, Vice City, Leonida, game logos, HUDs, or the
  game's named characters, and never describe a real person's likeness.
- Title and description must not claim the visuals are real gameplay.
"""


def _fmt_items(items: list[dict]) -> str:
    rows = []
    for it in items:
        m = it.get("meta") or {}
        rows.append(json.dumps({"id": it["id"], "tier": m.get("tier", "community"),
                                "source": it.get("channel"), "title": it.get("title"),
                                "summary": m.get("summary", "")[:700], "url": it.get("url")}))
    return "\n".join(rows)


def generate(items: list[dict], model: str = "claude-opus-5-5", effort: str = "medium") -> dict:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": "News items (JSON lines):\n" + _fmt_items(items)}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("script request was declined")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("script output truncated")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return json.loads(text)


def template(items: list[dict]) -> dict:
    """No-API fallback: one segment per headline, strongest sources first."""
    items = sorted(items, key=lambda i: -TIER_RANK.get((i.get("meta") or {}).get("tier"), 0))[:5]
    scenes = ["aerial shot over a neon-lit coastal city at sunset, palm trees, highway traffic",
              "sports car speeding along an oceanfront boulevard at night, neon reflections",
              "airboat racing through misty everglades-style swamp at golden hour",
              "crowded beach boardwalk, pastel art-deco hotels, handheld cinematic shot",
              "downtown skyline at night with rain-slick streets and police lights in the distance"]
    segs = [{"narration": f"{i.get('channel')}: {i.get('title')}.",
             "on_screen_text": " ".join((i.get("title") or "").split()[:6]),
             "visual_prompt": scenes[n % len(scenes)], "claim_ids": [i["id"]], "is_factual": True}
            for n, i in enumerate(items)]
    return {"title": "GTA 6 news you missed today", "hook": "Here's everything new on GTA 6 today.",
            "segments": segs, "description": "", "tags": []}


def validate(script: dict, items: list[dict]) -> dict:
    """Attach status/badges; drop factual segments without valid sources."""
    by_id = {i["id"]: i for i in items}
    kept, sources = [], {}
    for seg in script.get("segments", []):
        ids = [c for c in seg.get("claim_ids", []) if c in by_id]
        if seg.get("is_factual") and not ids:
            continue
        if ids:
            tiers = [(by_id[c].get("meta") or {}).get("tier", "community") for c in ids]
            weakest = min(tiers, key=lambda t: TIER_RANK.get(t, 0))
            seg["status"] = STATUS_BY_TIER.get(weakest, "rumor")
            for c in ids:
                sources[c] = by_id[c]
        else:
            seg["status"] = "opinion"
        seg["badge"] = BADGE.get(seg["status"], "")
        seg["claim_ids"] = ids
        kept.append(seg)
    if not kept:
        raise ValueError("no sourced segments left after validation")
    script["segments"] = kept
    script["sources"] = [{"id": s["id"], "title": s.get("title"), "url": s.get("url"),
                          "tier": (s.get("meta") or {}).get("tier")} for s in sources.values()]
    script["has_rumor"] = any(s["status"] == "rumor" for s in kept)
    return script


def write(items: list[dict], cfg) -> dict:
    sc = cfg["scripting"]
    items = items[: sc.get("max_items", 12)]
    if cfg.env("ANTHROPIC_API_KEY") or cfg.env("ANTHROPIC_AUTH_TOKEN"):
        raw = generate(items, sc["model"], sc.get("effort", "medium"))
    else:
        raw = template(items)
    return validate(raw, items)
