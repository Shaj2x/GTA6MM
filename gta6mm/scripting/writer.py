"""News items + official shot library -> a sourced, fast 15-25s Short script.

Structure that retains on Shorts/TikTok:
  hook (<=1.5s, bold claim or question, also on screen) -> 3-5 quick beats, each
  matched to real official shots -> payoff line that loops back to the hook.

Every factual line must cite the ids of the news items that support it. The
validator sets each beat's status from the *weakest* source it cites:
official -> CONFIRMED, press -> REPORTED, community -> RUMOR. Uncited factual
claims are dropped, so the channel never states a leak or guess as fact.
"""
from __future__ import annotations

import json
from typing import Any

from ..discovery.news import TIER_RANK

STATUS_BY_TIER = {"official": "confirmed", "press": "reported", "community": "rumor"}
BADGE = {"confirmed": "", "reported": "", "rumor": "RUMOR"}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook", "hook_text", "beats", "description", "tags"],
    "properties": {
        "title": {"type": "string", "description": "<=60 chars, curiosity gap, no false claims"},
        "hook": {"type": "string", "description": "first spoken line, <=9 words, a bold claim or question"},
        "hook_text": {"type": "string", "description": "on-screen hook, <=5 words, ALL CAPS"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "fact_text", "shot_ids", "ai_effect_prompt", "claim_ids", "is_factual"],
                "properties": {
                    "narration": {"type": "string", "description": "8-16 words, one concrete fact, spoken"},
                    "fact_text": {"type": "string",
                                  "description": "the beat's key fact as a 1-4 word ALL CAPS card, e.g. '39 SCREENSHOTS', 'NOV 19', 'AIRBOATS'"},
                    "shot_ids": {"type": "array", "items": {"type": "string"},
                                 "description": "1-3 library shot ids that match this line"},
                    "ai_effect_prompt": {"type": ["string", "null"],
                                         "description": "rare: a stylised effect shot only when no real shot fits"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                    "is_factual": {"type": "boolean"},
                },
            },
        },
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You write viral YouTube Shorts / TikTok scripts for a GTA 6 news channel, weeks before launch.

Format (20-28 seconds total, 70-95 spoken words, read fast):
- hook: the first spoken line. Lead with the single most surprising concrete fact as a bold claim
  or question that creates a curiosity gap. No greetings, no "hey guys", no filler.
- hook_text: 2-5 word ALL CAPS on-screen version of the hook.
- 5-7 beats, each 8-16 words, each delivering ONE concrete, new piece of information: a number,
  date, name, place, vehicle, weapon, mechanic or feature, said specifically (not "lots of cool
  stuff"). Pack as many distinct facts from the news as fit; no beat may just hype or repeat.
- fact_text: the beat's key fact as a 1-4 word ALL CAPS card shown on screen.
- The last beat pays off the hook and ends on a question or line that loops into the hook.
- title: <=60 chars, curiosity-driven, true.

Facts:
- Use ONLY the provided news items. Any beat stating something about the game sets
  is_factual=true and cites the supporting item ids in claim_ids. Never invent details.
- Items marked community are rumours or leaks: say "reportedly" / "rumour has it" and never
  present them as fact.

Visuals:
- For each beat pick 1-3 shot_ids from the official shot library whose descriptions match the
  narration. Prefer variety; don't reuse a shot within the video.
- ai_effect_prompt is null for almost every beat. Use it at most once, only for a stylised
  transition/effect shot when no real shot fits (e.g. "glowing neon map of a coastal state
  zooming in"). Never describe game logos, named characters or real people in it.
"""


def _fmt_items(items: list[dict]) -> str:
    return "\n".join(json.dumps({
        "id": it["id"], "tier": (it.get("meta") or {}).get("tier", "community"), "source": it.get("channel"),
        "title": it.get("title"), "summary": (it.get("meta") or {}).get("summary", "")[:700]})
        for it in items)


def _fmt_shots(shots: list[dict], limit: int = 160) -> str:
    rows = []
    for s in shots[:limit]:
        desc = s.get("description") or f"{s.get('title')} at {s.get('start', 0):.0f}s"
        tags = ", ".join(s.get("tags") or [])
        rows.append(f"{s['id']}: {desc}" + (f" [{tags}]" if tags else ""))
    return "\n".join(rows)


def style_note(profile: dict | None) -> str:
    if not profile:
        return ""
    words = int(profile.get("target_seconds", 25) * min(4.2, max(2.0, profile.get("words_per_second", 3.2))))
    return ("\n\nFORMAT REFERENCE - match this account's format (structure, hook style, pacing, tone, "
            "ending), but never copy their wording or content, and keep every rule above:\n"
            f"- Hook: {profile.get('hook_pattern')}\n- Structure: {profile.get('structure')}\n"
            f"- Tone: {profile.get('tone')}\n- Ending: {profile.get('ending_pattern')}\n"
            f"- Length: about {profile.get('target_seconds')} seconds, roughly {words} spoken words\n"
            f"- Guidance: {profile.get('script_guidance')}")


def generate(items: list[dict], shots: list[dict], model: str, effort: str, style: str = "") -> dict:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM + style,
        messages=[{"role": "user", "content":
                   "News items (JSON lines):\n" + _fmt_items(items)
                   + "\n\nOfficial shot library (id: description):\n" + (_fmt_shots(shots) or "(empty)")}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("script request was declined")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("script output truncated")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return json.loads(text)


def template(items: list[dict], shots: list[dict]) -> dict:
    """No-API fallback: three strongest headlines over library shots in order."""
    items = sorted(items, key=lambda i: -TIER_RANK.get((i.get("meta") or {}).get("tier"), 0))[:3]
    ids = [s["id"] for s in shots]
    beats = [{"narration": (i.get("title") or "").rstrip(".") + ".",
              "fact_text": " ".join((i.get("title") or "").upper().split()[:3]),
              "shot_ids": ids[n * 2:n * 2 + 2],
              "ai_effect_prompt": None, "claim_ids": [i["id"]], "is_factual": True}
             for n, i in enumerate(items)]
    return {"title": "GTA 6 news you missed", "hook": "Three GTA 6 updates you missed this week.",
            "hook_text": "YOU MISSED THIS", "beats": beats, "description": "", "tags": []}


def validate(script: dict, items: list[dict], shots: list[dict] | None = None,
             max_ai_clips: int = 1) -> dict:
    """Attach status/badges; drop unsourced factual beats, unknown shots and extra AI effects."""
    by_id = {i["id"]: i for i in items}
    shot_ids = {s["id"] for s in (shots or [])}
    kept, sources, ai_left, used = [], {}, max_ai_clips, set()
    for beat in script.get("beats", []):
        ids = [c for c in beat.get("claim_ids", []) if c in by_id]
        if beat.get("is_factual") and not ids:
            continue
        if ids:
            tiers = [(by_id[c].get("meta") or {}).get("tier", "community") for c in ids]
            weakest = min(tiers, key=lambda t: TIER_RANK.get(t, 0))
            beat["status"] = STATUS_BY_TIER.get(weakest, "rumor")
            for c in ids:
                sources[c] = by_id[c]
        else:
            beat["status"] = "opinion"
        beat["badge"] = BADGE.get(beat["status"], "")
        beat["claim_ids"] = ids
        beat["shot_ids"] = [s for s in beat.get("shot_ids", []) if s in shot_ids and s not in used]
        used.update(beat["shot_ids"])
        if beat.get("ai_effect_prompt"):
            if ai_left > 0:
                ai_left -= 1
            else:
                beat["ai_effect_prompt"] = None
        kept.append(beat)
    if not kept:
        raise ValueError("no sourced beats left after validation")
    script["beats"] = kept
    script["sources"] = [{"id": s["id"], "title": s.get("title"), "url": s.get("url"),
                          "tier": (s.get("meta") or {}).get("tier")} for s in sources.values()]
    script["has_rumor"] = any(b["status"] == "rumor" for b in kept)
    return script


def write(items: list[dict], shots: list[dict], cfg) -> dict:
    sc = cfg["scripting"]
    items = items[: sc.get("max_items", 12)]
    if cfg.env("ANTHROPIC_API_KEY") or cfg.env("ANTHROPIC_AUTH_TOKEN"):
        from ..study import load_profile
        raw = generate(items, shots, sc["model"], sc.get("effort", "medium"), style_note(load_profile(cfg)))
    else:
        raw = template(items, shots)
    return validate(raw, items, shots, cfg["shorts"]["max_ai_clips"])
