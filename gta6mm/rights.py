"""Rights gate. Every source gets a mode before any byte of it is rendered.

  owned       your own channel                      -> full clips
  licensed    creator gave written permission       -> full clips + attribution
  cc_by       YouTube Creative Commons licence      -> full clips + attribution
  commentary  opt-in; short excerpt under your VO   -> clip capped, VO required
  blocked     everything else                       -> outreach list, never rendered

Re-uploading other creators' streams without permission gets channels struck
and demonetised, which kills the profit this whole system is for, so the default
is closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decision:
    mode: str
    reason: str
    attribution: str = ""
    constraints: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.mode != "blocked"


def _match(entry: dict, src: dict) -> bool:
    if entry.get("platform") and entry["platform"] != src.get("platform"):
        return False
    if entry.get("channel_id") and entry["channel_id"] == src.get("channel_id"):
        return True
    ch = (entry.get("channel") or "").lower().lstrip("@")
    return bool(ch) and ch == (src.get("channel") or "").lower().lstrip("@")


def evaluate(src: dict, rights_cfg: dict) -> Decision:
    if src.get("kind") == "article":
        return Decision("blocked", "articles are script sources, not footage")
    who = f"@{src.get('channel')}" if src.get("channel") else "the original creator"
    for e in rights_cfg.get("block_channels", []) or []:
        if _match(e if isinstance(e, dict) else {"channel": e}, src):
            return Decision("blocked", "channel is on the block list")
    for e in rights_cfg.get("own_channels", []) or []:
        if _match(e, src):
            return Decision("owned", "your own channel")
    for e in rights_cfg.get("licensed_creators", []) or []:
        if _match(e, src):
            if not e.get("evidence"):
                return Decision("blocked", "licensed entry has no evidence recorded")
            return Decision("licensed", f"permission: {e['evidence']}", e.get("attribution") or who)
    if src.get("platform") == "youtube" and (src.get("meta") or {}).get("license") == "creativeCommon":
        return Decision("cc_by", "YouTube Creative Commons (CC BY) licence", who)
    if rights_cfg.get("allow_commentary"):
        c = rights_cfg.get("commentary", {})
        return Decision("commentary", "short excerpt for commentary/criticism", who, {
            "max_source_seconds": float(c.get("max_source_seconds", 12)),
            "require_voiceover": bool(c.get("require_voiceover", True)),
        })
    return Decision("blocked", "no licence or permission on file - add to outreach")


def outreach_message(src: dict, channel_handle: str) -> str:
    """Permission request to send a creator whose content is trending."""
    me = channel_handle or "our channel"
    return (
        f"Hey {src.get('channel')}! Your GTA 6 content (\"{src.get('title')}\") is blowing up. "
        f"I run {me}, a GTA 6 news/highlights channel. Would you be OK with us posting short "
        f"highlight clips from your streams/videos? Every clip credits you on screen and links "
        f"your channel in the description, and we're happy to discuss a revenue share. "
        f"If yes, just reply here and we'll add you to our approved list. Thanks!"
    )
