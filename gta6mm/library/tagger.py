"""Describe library shots once with Claude (vision) so scripts can pick matching footage."""
from __future__ import annotations

import base64
import json
from pathlib import Path

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["shots"],
    "properties": {"shots": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "description", "tags", "text_card"],
        "properties": {
            "id": {"type": "string"},
            "description": {"type": "string", "description": "<=14 words, what is visible"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "3-6 lowercase tags: location, vehicle, action, character, time of day"},
            "text_card": {"type": "boolean",
                          "description": "true for logos, title/rating cards, black or mostly-text frames"},
        }}}},
}

PROMPT = ("These are frames from official Grand Theft Auto VI trailers and screenshots. For each frame "
          "(labelled with its id), describe what is visible so an editor can match it to narration. "
          "Name the protagonists Jason or Lucia only if clearly them. Mark logos, title cards, rating "
          "cards and black frames as text_card.")


def _image_block(path: str) -> dict:
    data = Path(path).read_bytes()
    media = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    if path.lower().endswith(".webp"):
        media = "image/webp"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.b64encode(data).decode()}}


def tag_batch(client, model: str, shots: list[dict]) -> list[dict]:
    content: list[dict] = []
    for s in shots:
        content += [{"type": "text", "text": f"id: {s['id']}"}, _image_block(s["thumb"])]
    content.append({"type": "text", "text": PROMPT})
    resp = client.beta.messages.create(
        model=model, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"tagging stopped: {resp.stop_reason}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return json.loads(text)["shots"]


def tag_all(db, cfg, batch: int = 16, log=print) -> int:
    if not (cfg.env("ANTHROPIC_API_KEY") or cfg.env("ANTHROPIC_AUTH_TOKEN")):
        log("[library] no ANTHROPIC_API_KEY: shots stay untagged (scripts will pick them in order)")
        return 0
    import anthropic

    client = anthropic.Anthropic()
    todo = [s for s in db.shots(usable_only=True, untagged=True) if s.get("thumb")]
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        try:
            for r in tag_batch(client, cfg["scripting"]["model"], chunk):
                if db.get_shot(r["id"]):
                    db.tag_shot(r["id"], r["description"], r["tags"], not r["text_card"])
                    done += 1
        except Exception as exc:
            log(f"[library] tagging batch {i // batch} failed: {exc}")
    log(f"[library] tagged {done} shots")
    return done
