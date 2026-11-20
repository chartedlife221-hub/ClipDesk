"""Background jobs, persisted in SQLite so progress survives a page refresh and a server restart.

Two lanes, one worker thread each:
  analysis : import, transcribe, discover        (long CPU work)
  render   : preview, final render, ZIP export   (so a manual clip can render while discovery runs)
Cancel kills the running ffmpeg/yt-dlp step. Retry re-runs the SAME job id (attempt+1); every
handler is idempotent (outputs are keyed by content, written atomically), so retries and
restarts never duplicate outputs.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Callable

from .db import DB, new_id, now
from .media import Cancelled

log = logging.getLogger("clipdesk.jobs")

LANES = {"import_url": "analysis", "import_file": "analysis", "transcribe": "analysis",
         "discover": "analysis", "render": "render", "export": "render"}


class JobContext:
    def __init__(self, runner: "JobRunner", job: dict):
        self.runner, self.job = runner, job
        self.cancel = threading.Event()
        self._last = 0.0

    def progress(self, frac: float, message: str | None = None, stage: str | None = None, force=False):
        t = time.time()
        if not force and t - self._last < 0.4:
            return
        self._last = t
        row = {"progress": round(max(0.0, min(1.0, frac)), 4), "updated": now()}
        if message is not None:
            row["message"] = message[:300]
        if stage is not None:
            row["stage"] = stage
        self.runner.db.update("jobs", self.job["id"], row)
        if self.cancel.is_set():
            raise Cancelled("cancelled")


class JobRunner:
    def __init__(self, db: DB, handlers: dict[str, Callable[[JobContext], dict | None]]):
        self.db = db
        self.handlers = handlers
        self.active: dict[str, JobContext] = {}
        self._wake = {lane: threading.Event() for lane in set(LANES.values())}
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        n = self.db.exec("UPDATE jobs SET status='queued', message='resumed after server restart', updated=? "
                         "WHERE status IN ('running','cancelling')", [now()]).rowcount
        if n:
            log.info("re-queued %d interrupted job(s)", n)
        for lane in self._wake:
            t = threading.Thread(target=self._loop, args=(lane,), name=f"jobs-{lane}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        self._stop.set()
        for ctx in list(self.active.values()):
            ctx.cancel.set()
        for e in self._wake.values():
            e.set()

    # ---- API -------------------------------------------------------------
    def enqueue(self, kind: str, *, source_id=None, clip_id=None, payload=None, dedupe_key=None,
                message="queued") -> dict:
        if dedupe_key:
            existing = self.db.one("SELECT * FROM jobs WHERE dedupe_key=? AND status IN ('queued','running') "
                                   "ORDER BY created DESC", [dedupe_key])
            if existing:
                return existing
        jid = new_id("job")
        self.db.insert("jobs", {"id": jid, "kind": kind, "source_id": source_id, "clip_id": clip_id,
                                "payload": payload or {}, "status": "queued", "stage": "queued",
                                "message": message, "dedupe_key": dedupe_key, "created": now(), "updated": now()})
        self._wake[LANES[kind]].set()
        return self.db.one("SELECT * FROM jobs WHERE id=?", [jid])

    def cancel(self, jid: str) -> dict | None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", [jid])
        if not j:
            return None
        if j["status"] == "queued":
            self.db.update("jobs", jid, {"status": "cancelled", "message": "cancelled before start", "updated": now()})
        elif j["status"] == "running":
            self.db.update("jobs", jid, {"status": "cancelling", "message": "cancelling…", "updated": now()})
            if jid in self.active:
                self.active[jid].cancel.set()
        return self.db.one("SELECT * FROM jobs WHERE id=?", [jid])

    def retry(self, jid: str) -> dict | None:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", [jid])
        if not j or j["status"] not in ("failed", "cancelled"):
            return j
        self.db.update("jobs", jid, {"status": "queued", "error": "", "progress": 0, "stage": "queued",
                                     "message": f"retry (attempt {j['attempt'] + 1})",
                                     "attempt": j["attempt"] + 1, "updated": now()})
        self._wake[LANES[j["kind"]]].set()
        return self.db.one("SELECT * FROM jobs WHERE id=?", [jid])

    def wait(self, jid: str, timeout: float = 600) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.db.one("SELECT * FROM jobs WHERE id=?", [jid])
            if j and j["status"] in ("done", "failed", "cancelled"):
                return j
            time.sleep(0.2)
        raise TimeoutError(jid)

    # ---- worker ------------------------------------------------------------
    def _next(self, lane: str) -> dict | None:
        kinds = [k for k, l in LANES.items() if l == lane]
        q = ",".join("?" for _ in kinds)
        with self.db.tx():
            j = self.db.one(f"SELECT * FROM jobs WHERE status='queued' AND kind IN ({q}) ORDER BY created LIMIT 1", kinds)
            if j:
                self.db.update("jobs", j["id"], {"status": "running", "stage": "starting", "updated": now()})
        return j

    def _loop(self, lane: str):
        while not self._stop.is_set():
            j = self._next(lane)
            if not j:
                self._wake[lane].wait(1.0)
                self._wake[lane].clear()
                continue
            ctx = JobContext(self, j)
            self.active[j["id"]] = ctx
            try:
                result = self.handlers[j["kind"]](ctx) or {}
                cur = self.db.one("SELECT status FROM jobs WHERE id=?", [j["id"]])
                if cur and cur["status"] == "cancelling":
                    raise Cancelled("cancelled")
                self.db.update("jobs", j["id"], {"status": "done", "progress": 1.0, "stage": "done",
                                                 "message": result.pop("message", "done"), "result": result,
                                                 "updated": now()})
            except Cancelled:
                self.db.update("jobs", j["id"], {"status": "cancelled", "message": "cancelled", "updated": now()})
                self._on_fail(j, "cancelled")
            except Exception as e:  # noqa: BLE001
                log.error("job %s (%s) failed: %s\n%s", j["id"], j["kind"], e, traceback.format_exc())
                self.db.update("jobs", j["id"], {"status": "failed", "error": str(e)[:1000],
                                                 "message": f"failed: {str(e)[:200]}", "updated": now()})
                self._on_fail(j, str(e))
            finally:
                self.active.pop(j["id"], None)

    def _on_fail(self, job: dict, why: str):
        hook = self.handlers.get("_on_fail")
        if hook:
            try:
                hook(job, why)
            except Exception:  # noqa: BLE001
                log.exception("on_fail hook")
