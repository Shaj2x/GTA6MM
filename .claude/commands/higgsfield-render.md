---
description: Render queued GTA 6 news shorts through the Higgsfield connector and hand results back to the pipeline
---

You are the render worker for the gta6mm pipeline. Higgsfield is only reachable
through the Higgsfield MCP connector, so you run the jobs the Python side queued.

1. Run `python -m gta6mm.cli hf-pending` (or `--brief` for one line per job). It prints a JSON list of jobs with
   `job_id`, `tool` (`generate_video` or `generate_audio`), `params`, `est_credits`
   and, for jobs already submitted, `higgsfield_job_id`. If it is empty, say so and stop.
2. Call `mcp__Higgsfield__balance`. Add up `est_credits` of the `pending` jobs. If
   the balance is lower than that, stop and tell the user how many credits are
   needed; do not submit a partial set.
3. For jobs with status `pending`:
   - Videos (rare: at most one AI effect shot per Short): submit with
     `mcp__Higgsfield__generate_video_batch`, passing each job's `params` exactly as given.
     The Basic plan renders about 3 videos at once; on a 429 rate limit, wait and resubmit. Pass `use_unlim: false`
     unless the user has said to use unlimited generations.
   - Audio: pass `params` as given (they include voice_id and speech_rate). If `voice_id` is missing, call `mcp__Higgsfield__list_voices` once,
     pick an energetic English male or female preset suited to gaming news, and use
     that `voice_type`/`voice_id` for every audio job in this run. Submit with
     `mcp__Higgsfield__generate_audio_batch` (or `generate_audio`).
   - Immediately after each submission, record the mapping:
     `python -m gta6mm.cli hf-submitted <job_id> <higgsfield_job_id>`.
     On a transport timeout, do not resubmit; look the job up first.
4. Wait for all running jobs with `mcp__Higgsfield__jobs_wait` (groups of <= 12,
   timeout 15s, repeat while `all_terminal` is false).
5. For each finished job: `python -m gta6mm.cli hf-complete <job_id> --url <result_url>`.
   For a failed one: `python -m gta6mm.cli hf-complete <job_id> --error "<reason>"`.
   `hf-complete` downloads the file and assembles any short whose segments are all done.
6. Run `python -m gta6mm.cli review` and report the new posts (id, title) waiting
   for approval. Do not approve posts yourself.

## TikTok publishing (only when the user asks)

`data/higgsfield/tiktok_queue.json` lists approved TikTok posts (`file`, `title`,
`description`, `is_aigc`). For each:
1. Upload the local file with `mcp__Higgsfield__media_upload`, PUT the bytes with curl
   to the returned URL, then `mcp__Higgsfield__media_confirm`.
2. `mcp__Higgsfield__tiktok_accounts` to get the `connector_id`.
3. `mcp__Higgsfield__tiktok_prepare_publish` with `media_type: VIDEO`, the hosted
   `video_url`, `title`, `description`, and `is_aigc` from the queue entry (AI-visual
   shorts must be marked AI-generated). The user reviews and submits the publish form.
4. After the user confirms it posted: `python -m gta6mm.cli tiktok-done <post_id>`.
