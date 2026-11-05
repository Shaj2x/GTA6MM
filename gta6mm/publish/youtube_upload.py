"""YouTube resumable upload via the Data API (OAuth refresh token).

Sets status.containsSyntheticMedia for AI-visual posts, which is YouTube's
required "altered or synthetic content" disclosure. Leaving it off on realistic
AI footage risks removal and YPP suspension.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def access_token(cfg) -> str:
    cid = cfg.env("YOUTUBE_OAUTH_CLIENT_ID")
    secret = cfg.env("YOUTUBE_OAUTH_CLIENT_SECRET")
    refresh = cfg.env("YOUTUBE_OAUTH_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        raise RuntimeError("YouTube OAuth env vars not set (see .env.example)")
    r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                       "refresh_token": refresh, "grant_type": "refresh_token"},
                      timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def body(post: dict, privacy: str) -> dict:
    return {
        "snippet": {"title": post["title"], "description": post["description"],
                    "tags": post["tags"], "categoryId": "20"},  # 20 = Gaming
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": bool(post["synthetic"])},
    }


def upload(cfg, post: dict) -> str:
    token = access_token(cfg)
    path = Path(post["file"])
    size = os.path.getsize(path)
    init = requests.post(
        UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        data=json.dumps(body(post, cfg["publish"]["youtube_privacy"])), timeout=60)
    init.raise_for_status()
    with open(path, "rb") as fh:
        r = requests.put(init.headers["Location"], data=fh, timeout=1800,
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
    r.raise_for_status()
    return r.json()["id"]
