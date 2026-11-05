"""SQLite state store: discovered sources, render jobs, and posts."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,            -- "<platform>:<native id>"
  platform TEXT NOT NULL,         -- youtube | twitch | news
  kind TEXT NOT NULL,             -- video | stream | clip | article
  url TEXT NOT NULL,
  title TEXT,
  channel TEXT,
  channel_id TEXT,
  published_at REAL,
  views INTEGER DEFAULT 0,
  score REAL DEFAULT 0,
  meta TEXT DEFAULT '{}',
  rights_mode TEXT,               -- owned | licensed | cc_by | commentary | blocked
  rights_reason TEXT,
  discovered_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,             -- clip | hf_video | hf_audio | assemble
  status TEXT NOT NULL,           -- pending | running | done | failed | blocked
  parent_id TEXT,                 -- groups AI segments under an assemble job
  source_id TEXT,
  spec TEXT DEFAULT '{}',
  output TEXT,
  error TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  platform TEXT NOT NULL,         -- youtube | tiktok
  status TEXT NOT NULL,           -- awaiting_approval | approved | published | rejected | failed
  file TEXT NOT NULL,
  title TEXT,
  description TEXT,
  tags TEXT DEFAULT '[]',
  synthetic INTEGER DEFAULT 0,    -- contains AI-generated realistic media -> platform disclosure
  external_id TEXT,
  error TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, kind);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
"""

JSON_COLS = {"meta", "spec", "tags"}


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for c in JSON_COLS & d.keys():
            d[c] = json.loads(d[c] or "null")
        return d

    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [self._row(r) for r in self.conn.execute(sql, tuple(args))]

    # -- sources -------------------------------------------------------------
    def upsert_source(self, s: dict) -> None:
        now = time.time()
        meta = json.dumps(s.get("meta", {}))
        self.conn.execute(
            """INSERT INTO sources (id, platform, kind, url, title, channel, channel_id,
                 published_at, views, score, meta, discovered_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET views=excluded.views, score=excluded.score,
                 title=excluded.title, meta=excluded.meta, updated_at=excluded.updated_at""",
            (s["id"], s["platform"], s["kind"], s["url"], s.get("title"), s.get("channel"),
             s.get("channel_id"), s.get("published_at"), int(s.get("views") or 0),
             float(s.get("score") or 0), meta, now, now),
        )
        self.conn.commit()

    def set_rights(self, source_id: str, mode: str, reason: str) -> None:
        self.conn.execute("UPDATE sources SET rights_mode=?, rights_reason=? WHERE id=?",
                          (mode, reason, source_id))
        self.conn.commit()

    def get_source(self, source_id: str) -> dict | None:
        return self._row(self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())

    def top_sources(self, kinds: tuple[str, ...] = ("video", "stream", "clip"), limit: int = 50,
                    since: float = 0) -> list[dict]:
        q = ",".join("?" * len(kinds))
        return self._rows(
            f"SELECT * FROM sources WHERE kind IN ({q}) AND discovered_at>=? ORDER BY score DESC LIMIT ?",
            (*kinds, since, limit))

    def news(self, since: float, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM sources WHERE kind='article' AND published_at>=? "
                          "ORDER BY published_at DESC LIMIT ?", (since, limit))

    # -- jobs ----------------------------------------------------------------
    def add_job(self, kind: str, spec: dict, source_id: str | None = None,
                parent_id: str | None = None, status: str = "pending", job_id: str | None = None) -> str:
        jid = job_id or f"{kind}-{uuid.uuid4().hex[:10]}"
        now = time.time()
        self.conn.execute(
            "INSERT INTO jobs (id, kind, status, parent_id, source_id, spec, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (jid, kind, status, parent_id, source_id, json.dumps(spec), now, now))
        self.conn.commit()
        return jid

    def update_job(self, job_id: str, **fields: Any) -> None:
        if "spec" in fields:
            fields["spec"] = json.dumps(fields["spec"])
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
        self.conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        return self._row(self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self, kind: str | None = None, status: str | None = None,
             parent_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        for col, val in (("kind", kind), ("status", status), ("parent_id", parent_id)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        return self._rows(sql + " ORDER BY created_at", args)

    def has_job_for_source(self, source_id: str, kind: str) -> bool:
        return self.conn.execute("SELECT 1 FROM jobs WHERE source_id=? AND kind=? LIMIT 1",
                                 (source_id, kind)).fetchone() is not None

    def credits_queued_since(self, since: float) -> float:
        total = 0.0
        for j in self._rows("SELECT spec FROM jobs WHERE kind IN ('hf_video','hf_audio') "
                            "AND created_at>=? AND status!='failed'", (since,)):
            total += float(j["spec"].get("est_credits", 0))
        return total

    # -- posts ---------------------------------------------------------------
    def add_post(self, job_id: str, platform: str, file: str, title: str, description: str,
                 tags: list[str], synthetic: bool, status: str) -> str:
        pid = f"post-{uuid.uuid4().hex[:10]}"
        now = time.time()
        self.conn.execute(
            "INSERT INTO posts (id, job_id, platform, status, file, title, description, tags, synthetic, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, job_id, platform, status, file, title, description, json.dumps(tags), int(synthetic),
             now, now))
        self.conn.commit()
        return pid

    def update_post(self, post_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE posts SET {sets} WHERE id=?", (*fields.values(), post_id))
        self.conn.commit()

    def posts(self, status: str | None = None, platform: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM posts WHERE 1=1", []
        if status:
            sql += " AND status=?"
            args.append(status)
        if platform:
            sql += " AND platform=?"
            args.append(platform)
        return self._rows(sql + " ORDER BY created_at", args)

    def get_post(self, post_id: str) -> dict | None:
        return self._row(self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
