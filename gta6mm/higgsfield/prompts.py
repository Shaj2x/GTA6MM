"""IP-safe prompt hygiene for AI visuals.

Narration can name the game (that's news commentary). Generated *visuals* must
not imitate Rockstar's logos, HUD, or named characters, or a real person's face:
that's trademark/likeness trouble and gets videos taken down. We describe the
vibe (sunny neon Florida-style city) instead.
"""
from __future__ import annotations

import re

REPLACEMENTS = [
    (r"\bgrand theft auto\s*(vi|6)?\b", "open-world crime-drama"),
    (r"\bgta\s*(vi|6|v|5)?\b", "open-world crime-drama"),
    (r"\brockstar( games)?\b", ""),
    (r"\bvice city\b", "a neon-lit Miami-style coastal city"),
    (r"\bleonida\b", "a sunny Florida-style state"),
    (r"\bport gellhorn\b", "a run-down beach town"),
    (r"\bgrassrivers\b", "everglades-style swamplands"),
    (r"\blucia\b", "a determined young woman"),
    (r"\bjason\b", "a rugged man"),
    (r"\b(trevor|franklin|michael de santa|niko bellic|tommy vercetti|cj)\b", "a character"),
    (r"\b(hud|minimap|wanted stars?|game ui|loading screen|logo)\b", ""),
]

STYLE_SUFFIX = ("cinematic, photoreal, vertical 9:16, dynamic camera, golden-hour or neon night "
                "lighting, no text, no logos, no watermarks, no user interface")


def sanitize(prompt: str) -> str:
    p = prompt
    for pat, rep in REPLACEMENTS:
        p = re.sub(pat, rep, p, flags=re.I)
    p = re.sub(r"\s{2,}", " ", p).strip(" ,.")
    return f"{p}. {STYLE_SUFFIX}"


def flagged(prompt: str) -> list[str]:
    """Terms still present after sanitize (should be empty)."""
    return [pat for pat, _ in REPLACEMENTS[:10] if re.search(pat, prompt, re.I)]
