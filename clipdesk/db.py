"""SQLite persistence.

Truth vs. state (appendix §5): `candidates` rows are written once by discovery and never
updated (original metadata stays immutable). Everything a human changes lives in `revisions`
(append-only) and `clips` (pointer to the current / approved revision + status).
All timeline positions are stored in SOURCE seconds.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import data_root

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS creators (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'streamer',
  notes TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, creator_id TEXT REFERENCES creators(id),
  origin TEXT NOT NULL,              -- upload | youtube | twitch
  url TEXT, title TEXT NOT NULL DEFAULT '', duration REAL,
  status TEXT NOT NULL,              -- queued | importing | ready | live_waiting | inaccessible | failed
  status_detail TEXT NOT NULL DEFAULT '',
  media_path TEXT, play_path TEXT, poster_path TEXT,
  width INTEGER, height INTEGER, fps REAL, has_audio INTEGER,
  transcript_status TEXT NOT NULL DEFAULT 'none',   -- none | running | ready | unavailable | failed
  transcript_detail TEXT NOT NULL DEFAULT '',
  chat_status TEXT NOT NULL DEFAULT 'none',         -- none | ready
  content_kind TEXT NOT NULL DEFAULT 'auto',        -- auto | gaming | speech
  time_offset REAL NOT NULL DEFAULT 0,              -- seconds into the original stream (section imports)
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  text TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id), source_id TEXT NOT NULL,
  rank INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL, anchor REAL,
  score REAL NOT NULL, strength TEXT NOT NULL, title TEXT NOT NULL, reason TEXT NOT NULL,
  hashtags TEXT NOT NULL DEFAULT '[]', signals TEXT NOT NULL DEFAULT '{}',
  origin TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  candidate_id TEXT REFERENCES candidates(id),
  status TEXT NOT NULL DEFAULT 'pending',          -- pending | approved | rejected
  current_rev INTEGER, approved_rev INTEGER,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL REFERENCES clips(id),
  parent INTEGER, start REAL NOT NULL, end REAL NOT NULL,
  title TEXT NOT NULL DEFAULT '', hashtags TEXT NOT NULL DEFAULT '[]',
  captions TEXT NOT NULL DEFAULT '[]', layout TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL, kind TEXT NOT NULL,
  before TEXT NOT NULL, after TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS renders (
  id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, rev INTEGER NOT NULL, kind TEXT NOT NULL,
  key TEXT NOT NULL UNIQUE, path TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT, clip_id TEXT,
  payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,   -- queued|running|done|failed|cancelled
  stage TEXT NOT NULL DEFAULT '', progress REAL NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}',
  attempt INTEGER NOT NULL DEFAULT 1, dedupe_key TEXT,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clips_source ON clips(source_id);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_cand_req ON candidates(request_id);
"""

JSON_COLS = {"hashtags", "signals", "captions", "layout", "payload", "result", "before", "after"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def now() -> float:
    return time.time()


class DB:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or data_root() / "clipdesk.sqlite")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    @staticmethod
    def _enc(v: Any) -> Any:
        return json.dumps(v) if isinstance(v, (dict, list)) else v

    def one(self, sql: str, args: Iterable = ()) -> dict | None:
        with self._lock:
            return self._row(self.conn.execute(sql, tuple(args)).fetchone())

    def all(self, sql: str, args: Iterable = ()) -> list[dict]:
        with self._lock:
            return [self._row(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def exec(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(self._enc(a) for a in args))

    @contextmanager
    def tx(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def insert(self, table: str, row: dict) -> None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        self.exec(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(row.values()))

    def update(self, table: str, key: str, row: dict) -> None:
        sets = ",".join(f"{k}=?" for k in row)
        self.exec(f"UPDATE {table} SET {sets} WHERE id=?", [*row.values(), key])
