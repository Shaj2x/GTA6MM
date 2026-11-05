# GTA6MM

Python pipeline (`gta6mm/`) for GTA 6 launch content; see README.md.

- Tests: `pytest -q` (needs ffmpeg; no network).
- Higgsfield is only reachable through the Higgsfield MCP connector. The render
  worker steps are in `.claude/commands/higgsfield-render.md`; Python talks to it via
  `gta6mm hf-pending` / `hf-submitted` / `hf-complete`.
- Never approve posts, widen `rights` config, or set `auto_approve` on the user's behalf.
- Never put game logos, Rockstar character names, or real people's likenesses into
  generation prompts; route visual prompts through `higgsfield.prompts.sanitize`.
