"""Application logic shared by the HTTP layer, the job handlers and the tests."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np

from . import detect, ingest, media, score, transcribe
from .captions import build_srt, to_local
from .config import CFG, Settings, data_root
from .db import DB, new_id, now
from .jobs import JobContext, JobRunner
from .render import auto_speaker_cx, default_layout, render_clip, render_key

log = logging.getLogger("clipdesk.service")
ID_RE = re.compile(r"^[a-z]{2,5}_[0-9a-f]{10}$")


class UserError(ValueError):
    """Message is safe to show to the clipper."""


def check_id(v: str) -> str:
    if not isinstance(v, str) or not ID_RE.match(v):
        raise UserError("invalid id")
    return v


class App:
    def __init__(self, db: DB | None = None):
        self.db = db or DB()
        self.root = data_root()
        self.settings = Settings.load()
        self.jobs = JobRunner(self.db, {
            "import_url": self._job_import_url, "import_file": self._job_import_file,
            "transcribe": self._job_transcribe, "discover": self._job_discover,
            "render": self._job_render, "export": self._job_export, "_on_fail": self._job_failed,
        })

    # ================================================================ paths
    def src_dir(self, sid: str) -> Path:
        d = self.root / "sources" / check_id(sid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def transcript(self, sid: str) -> dict | None:
        return transcribe.load(self.src_dir(sid) / "transcript.json")

    def chat(self, sid: str) -> list[dict] | None:
        p = self.src_dir(sid) / "chat.json"
        return json.loads(p.read_text()) if p.exists() else None

    # ================================================================ creators
    def add_creator(self, name: str, kind: str = "streamer", notes: str = "") -> dict:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise UserError("Creator name is required (max 80 characters).")
        if kind not in ("streamer", "speaker", "motivator", "guest", "other"):
            kind = "other"
        cid = new_id("cr")
        self.db.insert("creators", {"id": cid, "name": name, "kind": kind, "notes": notes[:500], "created": now()})
        return self.db.one("SELECT * FROM creators WHERE id=?", [cid])

    def creators(self) -> list[dict]:
        return self.db.all("""SELECT c.*, COUNT(s.id) AS sources,
                              (SELECT COUNT(*) FROM clips k JOIN sources s2 ON s2.id=k.source_id
                               WHERE s2.creator_id=c.id AND k.status='approved') AS approved
                              FROM creators c LEFT JOIN sources s ON s.creator_id=c.id
                              GROUP BY c.id ORDER BY c.name COLLATE NOCASE""")

    # ================================================================ sources
    def _new_source(self, origin: str, url: str | None, creator_id: str | None, title: str,
                    content_kind: str) -> str:
        if creator_id:
            check_id(creator_id)
            if not self.db.one("SELECT id FROM creators WHERE id=?", [creator_id]):
                raise UserError("Unknown creator.")
        if content_kind not in ("auto", "gaming", "speech"):
            content_kind = "auto"
        sid = new_id("src")
        self.db.insert("sources", {"id": sid, "creator_id": creator_id, "origin": origin, "url": url,
                                   "title": title[:200], "status": "queued", "content_kind": content_kind,
                                   "created": now(), "updated": now()})
        return sid

    def add_url_source(self, url: str, creator_id: str | None = None, content_kind: str = "auto",
                       section: tuple[float, float] | None = None) -> dict:
        v = ingest.validate_url(url)  # raises SourceURLError (a ValueError) with a user message
        if section:
            s, e = section
            if s < 0 or e <= s:
                raise UserError("Section start must be before section end.")
        sid = self._new_source(v["origin"], v["url"], creator_id, v["url"], content_kind)
        if v["kind"] == "channel":
            self.db.update("sources", sid, {"status": "live_waiting", "updated": now(), "status_detail":
                           "This is a channel page, not a recording. When the stream ends, paste its VOD link "
                           "(twitch.tv/videos/…)."})
        else:
            self.jobs.enqueue("import_url", source_id=sid, payload={"section": list(section) if section else None},
                              dedupe_key=f"import:{sid}", message="waiting to import")
        return self.source(sid)

    def add_upload_source(self, filename: str, stream, creator_id: str | None = None,
                          content_kind: str = "auto") -> dict:
        ext = Path(filename or "").suffix.lower()
        if ext not in media.VIDEO_EXTS:
            raise UserError(f"Unsupported file type '{ext or '?'}'. Upload {', '.join(sorted(media.VIDEO_EXTS))}.")
        title = Path(filename).stem[:200] or "Upload"
        sid = self._new_source("upload", None, creator_id, title, content_kind)
        dest = self.src_dir(sid) / f"original{ext}"
        tmp = dest.with_suffix(ext + ".part")
        with open(tmp, "wb") as f:
            shutil.copyfileobj(stream, f, length=1024 * 1024)
        tmp.replace(dest)
        self.db.update("sources", sid, {"media_path": str(dest), "status": "importing", "updated": now(),
                                        "status_detail": "checking the file"})
        self.jobs.enqueue("import_file", source_id=sid, dedupe_key=f"import:{sid}", message="checking file")
        return self.source(sid)

    def source(self, sid: str) -> dict:
        s = self.db.one("""SELECT s.*, c.name AS creator_name, c.kind AS creator_kind FROM sources s
                           LEFT JOIN creators c ON c.id=s.creator_id WHERE s.id=?""", [check_id(sid)])
        if not s:
            raise UserError("Source not found.")
        s["has_media"] = bool(s.pop("play_path", None))
        s["has_poster"] = bool(s.pop("poster_path", None))
        s.pop("media_path", None)  # internal paths never leave the server
        s["job"] = self.db.one("SELECT id,kind,status,stage,progress,message,error FROM jobs WHERE source_id=? "
                               "AND kind IN ('import_url','import_file','transcribe','discover') "
                               "ORDER BY updated DESC LIMIT 1", [sid])
        s["clips"] = self.db.one("SELECT COUNT(*) n, SUM(status='approved') approved FROM clips WHERE source_id=?", [sid])
        return s

    def sources(self, creator_id: str | None = None) -> list[dict]:
        q = """SELECT s.id FROM sources s {w} ORDER BY s.updated DESC"""
        rows = self.db.all(q.format(w="WHERE s.creator_id=?" if creator_id else ""), [creator_id] if creator_id else [])
        return [self.source(r["id"]) for r in rows]

    def update_source(self, sid: str, data: dict) -> dict:
        row = {}
        if "title" in data:
            row["title"] = str(data["title"])[:200]
        if "creator_id" in data:
            cid = data["creator_id"] or None
            if cid and not self.db.one("SELECT id FROM creators WHERE id=?", [check_id(cid)]):
                raise UserError("Unknown creator.")
            row["creator_id"] = cid
        if data.get("content_kind") in ("auto", "gaming", "speech"):
            row["content_kind"] = data["content_kind"]
        if row:
            row["updated"] = now()
            self.db.update("sources", check_id(sid), row)
        return self.source(sid)

    def media_path(self, sid: str) -> Path:
        r = self.db.one("SELECT play_path FROM sources WHERE id=?", [check_id(sid)])
        if not r or not r["play_path"]:
            raise UserError("Media not ready.")
        return Path(r["play_path"])

    def attach_chat(self, sid: str, data: bytes, name: str) -> dict:
        try:
            msgs = ingest.parse_chat(data, name)
        except (ValueError, json.JSONDecodeError) as e:
            raise UserError(f"Could not read chat log: {e}") from e
        if not msgs:
            raise UserError("Chat log contained no timestamped messages.")
        off = self.db.one("SELECT time_offset FROM sources WHERE id=?", [check_id(sid)])["time_offset"] or 0
        msgs = [{"t": m["t"] - off, "text": m["text"]} for m in msgs]
        (self.src_dir(sid) / "chat.json").write_text(json.dumps(msgs))
        self.db.update("sources", sid, {"chat_status": "ready", "updated": now()})
        return {"messages": len(msgs), "first": msgs[0]["t"], "last": msgs[-1]["t"]}

    def attach_transcript(self, sid: str, data: bytes, name: str) -> dict:
        try:
            tr = transcribe.parse_caption_file(data, name)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            raise UserError(f"Could not read transcript: {e}") from e
        if not tr["words"]:
            raise UserError("Transcript file contained no timed text.")
        self._save_transcript(sid, tr)
        return {"words": len(tr["words"]), "engine": tr["engine"], "word_timing": tr["word_timing"]}

    def _save_transcript(self, sid: str, tr: dict):
        p = self.src_dir(sid) / "transcript.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(tr))
        tmp.replace(p)
        self.db.update("sources", sid, {"transcript_status": "ready", "updated": now(),
                                        "transcript_detail": f"{tr['engine']} · {len(tr['words'])} words · "
                                                             f"{tr['word_timing']} word timing"})

    def request_transcription(self, sid: str) -> dict:
        s = self.source(sid)
        if s["status"] != "ready":
            raise UserError("Source is not imported yet.")
        return self.jobs.enqueue("transcribe", source_id=sid, dedupe_key=f"transcribe:{sid}")

    def retry_source(self, sid: str) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE source_id=? AND kind IN ('import_url','import_file') "
                        "ORDER BY created DESC LIMIT 1", [check_id(sid)])
        if not j:
            raise UserError("Nothing to retry.")
        self.db.update("sources", sid, {"status": "queued", "status_detail": "retrying", "updated": now()})
        return self.jobs.retry(j["id"]) if j["status"] in ("failed", "cancelled") else \
            self.jobs.enqueue(j["kind"], source_id=sid, payload=j["payload"], dedupe_key=f"import:{sid}")

    def signals(self, sid: str) -> dict:
        p = self.src_dir(sid) / "rms.npy"
        if not p.exists():
            return {"window_s": CFG.window_s, "audio": [], "chat": []}
        rms = np.load(p)
        chat = self.chat(sid) or []
        cc, _, _ = detect.chat_buckets(chat, rms.size, CFG.window_s) if chat else (np.zeros(0), None, None)
        return {"window_s": CFG.window_s, "audio": [round(float(x), 1) for x in rms],
                "chat": [int(x) for x in cc]}

    # ---- import jobs -------------------------------------------------------
    def _finish_media(self, ctx: JobContext, sid: str, path: Path, base: float, span: float):
        info = media.probe(path)
        play = path
        if not media.browser_playable(info, path):
            ctx.progress(base, "making a browser-playable proxy", "import", force=True)
            play = path.parent / "proxy.mp4"
            if not play.exists():
                media.make_proxy(path, play, info, on_progress=lambda f: ctx.progress(base + span * f),
                                 cancel=ctx.cancel)
        poster = path.parent / "poster.jpg"
        try:
            media.make_poster(play, poster, min(info["duration"] * 0.1, 30))
        except media.MediaError:
            poster = None
        self.db.update("sources", sid, {
            "duration": info["duration"], "width": info["width"], "height": info["height"], "fps": info["fps"],
            "has_audio": int(info["has_audio"]), "play_path": str(play), "poster_path": str(poster) if poster else None,
            "status": "ready", "status_detail": f"{info['width']}×{info['height']} · {info['vcodec']}/{info['acodec']}",
            "updated": now()})
        # loudness windows for the gaming detector and the timeline heat-strip
        if info["has_audio"]:
            ctx.progress(base + span, "measuring loudness", "analysis", force=True)
            self._ensure_rms(sid, path, ctx)
        if transcribe.whisper_available() and info["has_audio"] and \
                self.db.one("SELECT transcript_status FROM sources WHERE id=?", [sid])["transcript_status"] != "ready":
            self.jobs.enqueue("transcribe", source_id=sid, dedupe_key=f"transcribe:{sid}")
        elif not transcribe.whisper_available():
            st = self.db.one("SELECT transcript_status FROM sources WHERE id=?", [sid])["transcript_status"]
            if st != "ready":
                self.db.update("sources", sid, {"transcript_status": "unavailable", "transcript_detail":
                               "faster-whisper not installed — upload an .srt/.vtt transcript or install it"})

    def _ensure_rms(self, sid: str, path: Path, ctx: JobContext | None = None) -> np.ndarray | None:
        p = self.src_dir(sid) / "rms.npy"
        if p.exists():
            return np.load(p)
        wav = self.src_dir(sid) / "audio16k.wav"
        if not wav.exists():
            info = media.probe(path)
            if not info["has_audio"]:
                return None
            media.extract_wav(path, wav, cancel=ctx.cancel if ctx else None)
        samples, sr = media.read_wav_mono16(wav)
        rms = media.rms_windows(samples, sr, CFG.window_s)
        np.save(p, rms)
        return rms

    def _job_import_file(self, ctx: JobContext) -> dict:
        sid = ctx.job["source_id"]
        row = self.db.one("SELECT media_path FROM sources WHERE id=?", [sid])
        path = Path(row["media_path"])
        self.db.update("sources", sid, {"status": "importing", "status_detail": "checking the file", "updated": now()})
        ctx.progress(0.05, "probing media", "import", force=True)
        try:
            media.probe(path)
        except media.MediaError as e:
            raise UserError(str(e)) from e
        self._finish_media(ctx, sid, path, 0.1, 0.8)
        return {"message": "imported"}

    def _job_import_url(self, ctx: JobContext) -> dict:
        sid = ctx.job["source_id"]
        src = self.db.one("SELECT * FROM sources WHERE id=?", [sid])
        section = ctx.job["payload"].get("section")
        if not ingest.ytdlp_available():
            raise UserError("yt-dlp is not installed, so URL import is unavailable. "
                            "Run `pip install yt-dlp` (see README) or upload the video file.")
        self.db.update("sources", sid, {"status": "importing", "status_detail": "checking the link", "updated": now()})
        ctx.progress(0.02, "checking availability", "import", force=True)
        try:
            info = ingest.ytdlp_probe(src["url"])
        except Exception as e:  # noqa: BLE001
            status = ingest.classify_ytdlp_error(str(e))
            raise UserError(f"[{status}] {_clean_ytdlp(str(e))}") from e
        state, detail = ingest.classify_info(info)
        title = info.get("title") or src["title"]
        self.db.update("sources", sid, {"title": title[:200], "duration": info.get("duration"), "updated": now()})
        if state != "ready_to_download":
            self.db.update("sources", sid, {"status": state, "status_detail": detail, "updated": now()})
            return {"message": detail, "status": state}
        d = self.src_dir(sid)
        try:
            ingest.ytdlp_download(src["url"], d, section=tuple(section) if section else None,
                                  on_progress=lambda f, m: ctx.progress(0.05 + 0.6 * f, m, "import"),
                                  cancel=ctx.cancel)
        except Exception as e:  # noqa: BLE001
            if ctx.cancel.is_set():
                raise media.Cancelled("cancelled") from e
            status = ingest.classify_ytdlp_error(str(e))
            raise UserError(f"[{status}] {_clean_ytdlp(str(e))}") from e
        vid = next((p for p in sorted(d.glob("original.*")) if p.suffix.lower() in media.VIDEO_EXTS), None)
        if not vid:
            raise UserError("Download finished but no video file was produced.")
        offset = float(section[0]) if section else 0.0
        self.db.update("sources", sid, {"media_path": str(vid), "time_offset": offset, "updated": now()})
        for sub in sorted(d.glob("original.*")):
            low = sub.name.lower()
            try:
                if "live_chat" in low or "rechat" in low:
                    self.attach_chat(sid, sub.read_bytes(), sub.name)
                elif low.endswith((".vtt", ".json3", ".srt")) and \
                        self.db.one("SELECT transcript_status FROM sources WHERE id=?", [sid])["transcript_status"] != "ready":
                    tr = transcribe.parse_caption_file(sub.read_bytes(), sub.name)
                    if tr["words"]:
                        if offset:
                            for w in tr["words"]:
                                w["s"] -= offset
                                w["e"] -= offset
                        tr["engine"] = "youtube-captions:" + sub.name
                        self._save_transcript(sid, tr)
            except Exception as e:  # noqa: BLE001
                log.warning("sidecar %s ignored: %s", sub.name, e)
        self._finish_media(ctx, sid, vid, 0.7, 0.25)
        return {"message": "imported"}

    def _job_transcribe(self, ctx: JobContext) -> dict:
        sid = ctx.job["source_id"]
        src = self.db.one("SELECT * FROM sources WHERE id=?", [sid])
        self.db.update("sources", sid, {"transcript_status": "running", "transcript_detail": "starting", "updated": now()})
        try:
            path = Path(src["media_path"])
            wav = self.src_dir(sid) / "audio16k.wav"
            if not wav.exists():
                ctx.progress(0.01, "extracting audio", "transcribe", force=True)
                media.extract_wav(path, wav, cancel=ctx.cancel)
            tr = transcribe.transcribe_whisper(wav, src["duration"] or 1, self.settings.whisper_model,
                                               CFG.whisper_compute,
                                               lambda f, m: ctx.progress(f, m, "transcribe"), ctx.cancel)
        except transcribe.TranscriptUnavailable as e:
            self.db.update("sources", sid, {"transcript_status": "unavailable", "transcript_detail": str(e)})
            raise UserError(str(e)) from e
        except Exception as e:
            self.db.update("sources", sid, {"transcript_status": "failed", "transcript_detail": str(e)[:300]})
            raise
        self._save_transcript(sid, tr)
        return {"message": f"transcribed {len(tr['words'])} words", "words": len(tr["words"])}

    def _job_failed(self, job: dict, why: str):
        if job["kind"] in ("import_url", "import_file") and job.get("source_id"):
            m = re.match(r"^\[(live_waiting|inaccessible|failed)\]\s*(.*)$", why, re.S)
            status, detail = (m.group(1), m.group(2)) if m else ("failed", why)
            if why == "cancelled":
                status, detail = "failed", "Import cancelled — retry to start again."
            self.db.update("sources", job["source_id"], {"status": status, "status_detail": detail[:500], "updated": now()})
        if job["kind"] == "discover":
            rid = job["payload"].get("request_id")
            if rid:
                self.db.update("requests", rid, {"status": "failed", "note": why[:300]})

    # ================================================================ discovery
    def find(self, sid: str, text: str, mode: str = "auto") -> dict:
        s = self.source(sid)
        if s["status"] != "ready":
            raise UserError("Source is not ready yet — import must finish first.")
        text = (text or "").strip()[:300]
        if not text:
            raise UserError("Describe the moment you want.")
        if mode not in ("auto", "gaming", "speech"):
            mode = "auto"
        rid = new_id("req")
        self.db.insert("requests", {"id": rid, "source_id": sid, "text": text, "mode": mode, "status": "queued",
                                    "created": now()})
        self.jobs.enqueue("discover", source_id=sid, payload={"request_id": rid}, dedupe_key=f"discover:{rid}")
        return self.request(rid)

    def request(self, rid: str) -> dict:
        r = self.db.one("SELECT * FROM requests WHERE id=?", [check_id(rid)])
        if not r:
            raise UserError("Request not found.")
        r["candidates"] = self.db.all("""SELECT c.*, k.id AS clip_id, k.status AS clip_status FROM candidates c
                                         LEFT JOIN clips k ON k.candidate_id=c.id
                                         WHERE c.request_id=? ORDER BY c.rank""", [rid])
        r["job"] = self.db.one("SELECT id,status,stage,progress,message,error FROM jobs WHERE dedupe_key=? "
                               "ORDER BY created DESC", [f"discover:{rid}"])
        return r

    def requests(self, sid: str) -> list[dict]:
        return [self.request(r["id"]) for r in
                self.db.all("SELECT id FROM requests WHERE source_id=? ORDER BY created DESC LIMIT 20", [check_id(sid)])]

    def resolve_mode(self, src: dict, mode: str, transcript: dict | None) -> str:
        if mode in ("gaming", "speech"):
            return mode
        if src["content_kind"] in ("gaming", "speech"):
            return src["content_kind"]
        if src["chat_status"] == "ready":
            return "gaming"
        cr = self.db.one("SELECT kind FROM creators WHERE id=?", [src["creator_id"]]) if src["creator_id"] else None
        if cr and cr["kind"] == "streamer":
            return "gaming"
        return "speech" if transcript else "gaming"

    def _job_discover(self, ctx: JobContext) -> dict:
        rid = ctx.job["payload"]["request_id"]
        req = self.db.one("SELECT * FROM requests WHERE id=?", [rid])
        if self.db.one("SELECT COUNT(*) n FROM candidates WHERE request_id=?", [rid])["n"]:
            return {"message": "already discovered"}  # idempotent on retry/restart
        sid = req["source_id"]
        src = self.db.one("SELECT * FROM sources WHERE id=?", [sid])
        self.db.update("requests", rid, {"status": "running"})
        tr = self.transcript(sid)
        mode = self.resolve_mode(src, req["mode"], tr)
        creator = (self.db.one("SELECT name FROM creators WHERE id=?", [src["creator_id"]]) or {}).get("name", "") \
            if src["creator_id"] else ""
        if mode == "speech" and not tr:
            if transcribe.whisper_available() and src["has_audio"]:
                ctx.progress(0.02, "transcribing first (speech search needs a transcript)", "transcribe", force=True)
                self._job_transcribe(ctx)
                tr = self.transcript(sid)
            else:
                note = ("No transcript yet: speech search needs one. Install faster-whisper or upload an "
                        ".srt/.vtt file on this source. Manual clipping works now.")
                self.db.update("requests", rid, {"status": "done", "note": note, "provider": "none"})
                return {"message": note}
        ctx.progress(0.6, "scoring moments", "analysis", force=True)
        dur = float(src["duration"] or 0)
        if mode == "speech":
            res = detect.speech_search(tr, req["text"], dur, creator=creator)
        else:
            rms = self._ensure_rms(sid, Path(src["media_path"]), ctx) if src["has_audio"] else None
            if rms is None:
                res = detect.DiscoveryResult([], "No audio track — the reaction detector has nothing to measure.", "gaming")
            else:
                res = detect.gaming_detect(rms, self.chat(sid), dur, req["text"], tr, creator=creator)
        ctx.progress(0.8, "ranking", "rank", force=True)
        cands, provider = score.rerank(res.candidates, req["text"], tr, self.settings)
        layout_mode = "gameplay" if mode == "gaming" else "speaker"
        with self.db.tx():
            for rank, c in enumerate(cands, 1):
                cid = new_id("cand")
                self.db.insert("candidates", {
                    "id": cid, "request_id": rid, "source_id": sid, "rank": rank, "start": c.start, "end": c.end,
                    "anchor": c.anchor, "score": c.score, "strength": c.strength, "title": c.title,
                    "reason": c.reason, "hashtags": c.hashtags, "signals": c.signals, "origin": f"discovered:{mode}",
                    "created": now()})
                self._create_clip(sid, c.start, c.end, title=c.title, hashtags=c.hashtags,
                                  candidate_id=cid, layout=self._layout_for(sid, layout_mode), transcript=tr)
        self.db.update("requests", rid, {"status": "done", "note": res.note, "provider": provider, "mode": mode})
        return {"message": res.note, "candidates": len(cands)}

    # ================================================================ clips
    def _create_clip(self, sid, start, end, *, title="", hashtags=None, candidate_id=None, layout=None,
                     transcript=None) -> str:
        kid = new_id("clp")
        tr = transcript if transcript is not None else self.transcript(sid)
        caps = transcribe.caption_lines(tr, start, end) if tr else []
        self.db.insert("clips", {"id": kid, "source_id": sid, "candidate_id": candidate_id, "status": "pending",
                                 "created": now(), "updated": now()})
        rev = self._insert_rev(kid, None, {"start": start, "end": end, "title": title or "", "hashtags": hashtags or [],
                                           "captions": caps, "layout": layout or default_layout("speaker")})
        self.db.update("clips", kid, {"current_rev": rev})
        return kid

    def _insert_rev(self, kid, parent, r) -> int:
        cur = self.db.exec("INSERT INTO revisions (clip_id,parent,start,end,title,hashtags,captions,layout,created) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           [kid, parent, round(float(r["start"]), 3), round(float(r["end"]), 3), r.get("title", ""),
                            r.get("hashtags", []), r.get("captions", []), r.get("layout", {}), now()])
        return cur.lastrowid

    def create_manual_clip(self, sid: str, start: float, end: float, layout_mode: str | None = None,
                           snap: bool = False) -> dict:
        s = self.source(sid)
        if s["status"] != "ready":
            raise UserError("Source is not ready yet.")
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError) as e:
            raise UserError("Start and end must be numbers of seconds.") from e
        err = detect.manual_bounds_check(start, end, float(s["duration"]))
        if err:
            raise UserError(err)
        note = ""
        if snap:
            start, end, note = self.snap_edges(sid, start, end, float(s["duration"]))
        mode = layout_mode or ("gameplay" if self.resolve_mode(self.db.one("SELECT * FROM sources WHERE id=?", [sid]),
                                                                  "auto", self.transcript(sid)) == "gaming"
                               else self.settings.default_layout)
        tr = self.transcript(sid)
        title = ""
        if tr:
            sents = [x for x in transcribe.sentences(tr) if x["e"] > start and x["s"] < end]
            if sents:
                title = detect._title_from(max(sents, key=lambda x: len(x["text"]))["text"])
        kid = self._create_clip(sid, start, end, title=title or f"Clip at {detect.fmt_tc(start)}",
                                hashtags=["#shorts", "#clips"], layout=self._layout_for(sid, mode), transcript=tr)
        k = self.clip(kid)
        k["snap_note"] = note
        return k

    def clip(self, kid: str) -> dict:
        k = self.db.one("""SELECT k.*, s.title AS source_title, s.duration AS source_duration, s.time_offset,
                           s.creator_id, c.name AS creator_name FROM clips k JOIN sources s ON s.id=k.source_id
                           LEFT JOIN creators c ON c.id=s.creator_id WHERE k.id=?""", [check_id(kid)])
        if not k:
            raise UserError("Clip not found.")
        k["rev"] = self.db.one("SELECT * FROM revisions WHERE id=?", [k["current_rev"]])
        k["approved"] = self.db.one("SELECT * FROM revisions WHERE id=?", [k["approved_rev"]]) if k["approved_rev"] else None
        k["candidate"] = self.db.one("SELECT * FROM candidates WHERE id=?", [k["candidate_id"]]) if k["candidate_id"] else None
        k["can_undo"] = bool(self.db.one("SELECT id FROM actions WHERE clip_id=? AND undone=0 LIMIT 1", [kid]))
        k["edited_since_approval"] = bool(k["approved_rev"] and k["approved_rev"] != k["current_rev"])
        k["renders"] = self.db.all("SELECT id,rev,kind,status,created FROM renders WHERE clip_id=? AND status='done' "
                                   "ORDER BY created DESC", [kid])
        k["job"] = self.db.one("SELECT id,kind,status,stage,progress,message,error,payload FROM jobs WHERE clip_id=? "
                               "ORDER BY updated DESC LIMIT 1", [kid])
        return k

    def clips(self, source_id: str | None = None, status: str | None = None, creator_id: str | None = None) -> list[dict]:
        w, a = [], []
        if source_id:
            w.append("k.source_id=?")
            a.append(check_id(source_id))
        if status in ("pending", "approved", "rejected"):
            w.append("k.status=?")
            a.append(status)
        if creator_id:
            w.append("s.creator_id=?")
            a.append(check_id(creator_id))
        rows = self.db.all(f"""SELECT k.id, k.source_id, k.status, k.current_rev, k.approved_rev, k.updated,
                 r.start, r.end, r.title, r.hashtags, s.title AS source_title, s.time_offset, c.name AS creator_name,
                 cd.score, cd.strength, cd.reason, cd.rank, cd.signals, cd.origin, q.text AS request_text,
                 (SELECT COUNT(*) FROM renders rr WHERE rr.clip_id=k.id AND rr.kind='final' AND rr.rev=k.approved_rev
                  AND rr.status='done') > 0 AS exported
                 FROM clips k JOIN revisions r ON r.id=k.current_rev JOIN sources s ON s.id=k.source_id
                 LEFT JOIN creators c ON c.id=s.creator_id LEFT JOIN candidates cd ON cd.id=k.candidate_id
                 LEFT JOIN requests q ON q.id=cd.request_id
                 {"WHERE " + " AND ".join(w) if w else ""}
                 ORDER BY k.created DESC, cd.rank""", a)
        return rows

    def revise(self, kid: str, data: dict) -> dict:
        k = self.clip(kid)
        cur = k["rev"]
        new = {key: cur[key] for key in ("start", "end", "title", "hashtags", "captions", "layout")}
        dur = float(k["source_duration"])
        snap_note = ""
        if "start" in data or "end" in data:
            try:
                s = float(data.get("start", cur["start"]))
                e = float(data.get("end", cur["end"]))
            except (TypeError, ValueError) as ex:
                raise UserError("Start and end must be numbers of seconds.") from ex
            err = detect.manual_bounds_check(s, e, dur)
            if err:
                raise UserError(err)
            if data.get("snap"):
                which = {w for w, key in (("s", "start"), ("e", "end"))
                         if key in data and abs(float(data[key]) - float(cur[key])) > 1e-3}
                s, e, snap_note = self.snap_edges(k["source_id"], s, e, dur, which)
            new["start"], new["end"] = round(s, 3), round(e, 3)
        if "title" in data:
            new["title"] = str(data["title"])[:120]
        if "hashtags" in data:
            tags = data["hashtags"]
            if isinstance(tags, str):
                tags = tags.split()
            new["hashtags"] = [("#" + re.sub(r"[^\w]", "", t.lstrip("#")))[:40] for t in tags if re.sub(r"[^\w]", "", t)][:15]
        if "captions" in data:
            caps = []
            for c in data["captions"] or []:
                try:
                    cs, ce = float(c["s"]), float(c["e"])
                except (KeyError, TypeError, ValueError) as ex:
                    raise UserError("Each caption needs numeric s and e (source seconds).") from ex
                if ce <= cs:
                    raise UserError(f"Caption '{str(c.get('text',''))[:30]}' ends before it starts.")
                caps.append({"s": round(cs, 3), "e": round(ce, 3), "text": str(c.get("text", ""))[:200]})
            new["captions"] = sorted(caps, key=lambda c: c["s"])
        elif ("start" in data or "end" in data):
            new["captions"] = self._refresh_captions(k["source_id"], new["captions"], new["start"], new["end"])
        if "layout" in data and isinstance(data["layout"], dict):
            lay = {**(new["layout"] or {}), **data["layout"]}
            if data["layout"].get("mode") and data["layout"]["mode"] != (cur["layout"] or {}).get("mode"):
                lay = {**default_layout(data["layout"]["mode"]), **data["layout"]}
            new["layout"] = lay
            self._remember_layout(k["source_id"], lay)
        if new == {key: cur[key] for key in new}:
            k["snap_note"] = snap_note
            return k
        rev = self._insert_rev(kid, cur["id"], new)
        self.db.update("clips", kid, {"current_rev": rev, "updated": now()})
        self._log_action(kid, "edit", {"rev": cur["id"]}, {"rev": rev})
        out = self.clip(kid)
        out["snap_note"] = snap_note
        return out

    # ---- framing memory ---------------------------------------------------
    REMEMBER = {"speaker": ("cx", "cy", "zoom", "caption_y"), "gameplay": ("face", "action", "split", "caption_y"),
                "fit": ("caption_y",)}

    def _layout_for(self, sid: str, mode: str) -> dict:
        """Default layout for a new clip, with the framing last used on this source (fixes off-centre speakers)."""
        lay = default_layout(mode)
        r = self.db.one("SELECT layout_memory FROM sources WHERE id=?", [sid])
        mem = (r or {}).get("layout_memory") or {}
        lay.update({k: v for k, v in (mem.get(mode) or {}).items() if k in self.REMEMBER.get(mode, ())})
        return lay

    def _remember_layout(self, sid: str, lay: dict) -> None:
        mode = lay.get("mode")
        if mode not in self.REMEMBER:
            return
        r = self.db.one("SELECT layout_memory FROM sources WHERE id=?", [sid])
        mem = (r or {}).get("layout_memory") or {}
        mem[mode] = {k: lay[k] for k in self.REMEMBER[mode] if k in lay}
        self.db.update("sources", sid, {"layout_memory": mem})

    # ---- snapping manual edges to pauses -----------------------------------
    def _energy(self, sid: str) -> np.ndarray | None:
        """20 ms loudness envelope (dBFS) used to find pauses when there is no transcript."""
        p = self.src_dir(sid) / "energy20ms.npy"
        if p.exists():
            return np.load(p)
        wav = self.src_dir(sid) / "audio16k.wav"
        if not wav.exists():
            src = self.db.one("SELECT media_path, has_audio FROM sources WHERE id=?", [sid])
            if not src or not src["has_audio"]:
                return None
            media.extract_wav(Path(src["media_path"]), wav)
        samples, sr = media.read_wav_mono16(wav)
        env = media.rms_windows(samples, sr, 0.02)
        np.save(p, env)
        return env

    def snap_edges(self, sid: str, s: float, e: float, dur: float,
                   which: set[str] | None = None) -> tuple[float, float, str]:
        """Move start/end out of words: to the word edge (transcript) or the quietest 20 ms nearby (audio).

        Never moves an edge more than 1.2 s, and keeps the original if snapping would break the limits.
        """
        which = {"s", "e"} if which is None else which
        tr = self.transcript(sid)
        s0, e0, how = s, e, ""
        words = (tr or {}).get("words") or []
        if words:
            if "s" in which:
                w = next((w for w in words if w["s"] - 0.05 <= s < w["e"]), None)
                if w and abs(s - w["s"]) <= 1.2:
                    i = words.index(w)
                    prev_end = words[i - 1]["e"] if i else 0.0
                    s = max(prev_end + 0.02, w["s"] - 0.12)
            if "e" in which:
                w = next((w for w in words if w["s"] < e <= w["e"] + 0.05), None)
                if w and abs(w["e"] - e) <= 1.2:
                    i = words.index(w)
                    nxt = words[i + 1]["s"] if i + 1 < len(words) else dur
                    e = min(nxt - 0.02, w["e"] + 0.15)
            how = "word edges"
        else:
            env = self._energy(sid)
            if env is not None and env.size:
                def quiet(t):
                    i0, i1 = max(0, int((t - 0.5) / 0.02)), min(env.size, int((t + 0.5) / 0.02) + 1)
                    if i1 <= i0:
                        return t
                    seg = env[i0:i1]
                    if seg.max() - seg.min() < 6:   # no real pause nearby: leave it
                        return t
                    return (i0 + int(np.argmin(seg))) * 0.02 + 0.01
                if "s" in which:
                    s = quiet(s)
                if "e" in which:
                    e = quiet(e)
                how = "audio pauses"
        s, e = max(0.0, round(s, 3)), min(dur, round(e, 3))
        if detect.manual_bounds_check(s, e, dur):
            return s0, e0, ""
        moved = [f"{lbl} {abs(b - a):.2f}s" for lbl, a, b in (("start", s0, s), ("end", e0, e)) if abs(b - a) >= 0.01]
        return s, e, (f"Snapped to {how}: " + ", ".join(moved)) if moved else ""

    # ---- filmstrip thumbnails ------------------------------------------------
    def strip(self, sid: str, s: float, e: float, n: int = 10, h: int = 64) -> Path:
        """One JPEG of n frames side by side between s and e (cached) for the trim timeline."""
        src = self.db.one("SELECT play_path, duration, width, height FROM sources WHERE id=?", [check_id(sid)])
        if not src or not src["play_path"]:
            raise UserError("Media not ready.")
        dur = float(src["duration"])
        s, e = max(0.0, float(s)), min(dur, float(e))
        if e - s < 0.5:
            raise UserError("Range too short.")
        n, h = max(4, min(16, int(n))), max(32, min(120, int(h)))
        d = self.src_dir(sid) / "strips"
        d.mkdir(exist_ok=True)
        out = d / f"{s:.1f}_{e:.1f}_{n}_{h}.jpg"
        if out.exists():
            return out
        args = []
        for i in range(n):
            t = min(dur - 0.05, s + (i + 0.5) * (e - s) / n)
            args += ["-ss", f"{t:.2f}", "-i", src["play_path"]]
        chains = ";".join(f"[{i}:v]scale=-2:{h},setsar=1,trim=end_frame=1[f{i}]" for i in range(n))
        graph = chains + ";" + "".join(f"[f{i}]" for i in range(n)) + f"hstack=inputs={n}[v]"
        tmp = out.with_suffix(".part.jpg")
        media.run_ffmpeg([*args, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-q:v", "5", str(tmp)])
        tmp.replace(out)
        return out

    def _refresh_captions(self, sid, caps, s, e):
        """Keep (possibly edited) lines that still overlap; fill newly uncovered time from the transcript."""
        kept = [c for c in caps if c["e"] > s and c["s"] < e]
        tr = self.transcript(sid)
        if not tr:
            return kept
        fresh = transcribe.caption_lines(tr, s, e)
        add = [f for f in fresh if not any(min(f["e"], c["e"]) - max(f["s"], c["s"]) > 0.05 for c in kept)]
        return sorted(kept + add, key=lambda c: c["s"])

    def auto_center(self, kid: str) -> dict:
        k = self.clip(kid)
        src = self.db.one("SELECT media_path FROM sources WHERE id=?", [k["source_id"]])
        cx, note = auto_speaker_cx(Path(src["media_path"]), k["rev"]["start"], k["rev"]["end"])
        if cx is None:
            return {"clip": k, "note": note}
        return {"clip": self.revise(kid, {"layout": {"mode": "speaker", "cx": round(cx, 3), "auto_cx": round(cx, 3)}}),
                "note": note}

    def set_status(self, kid: str, status: str) -> dict:
        if status not in ("pending", "approved", "rejected"):
            raise UserError("status must be pending, approved or rejected")
        k = self.clip(kid)
        before = {"status": k["status"], "approved_rev": k["approved_rev"]}
        after = {"status": status, "approved_rev": k["current_rev"] if status == "approved" else None}
        if before == after:
            return k
        self.db.update("clips", kid, {**after, "updated": now()})
        self._log_action(kid, "status", before, after)
        return self.clip(kid)

    def _log_action(self, kid, kind, before, after):
        self.db.insert("actions", {"clip_id": kid, "kind": kind, "before": before, "after": after, "created": now()})

    def undo(self, kid: str) -> dict:
        a = self.db.one("SELECT * FROM actions WHERE clip_id=? AND undone=0 ORDER BY id DESC LIMIT 1", [check_id(kid)])
        if not a:
            raise UserError("Nothing to undo for this clip.")
        if a["kind"] == "edit":
            self.db.update("clips", kid, {"current_rev": a["before"]["rev"], "updated": now()})
        else:
            self.db.update("clips", kid, {**a["before"], "updated": now()})
        self.db.exec("UPDATE actions SET undone=1 WHERE id=?", [a["id"]])
        return self.clip(kid)

    def history(self, kid: str) -> list[dict]:
        return self.db.all("SELECT id,kind,before,after,undone,created FROM actions WHERE clip_id=? ORDER BY id DESC",
                           [check_id(kid)])

    # ================================================================ rendering
    def request_render(self, kid: str, kind: str) -> dict:
        k = self.clip(kid)
        if kind == "final":
            if k["status"] != "approved":
                raise UserError("Approve the clip first — finals render from the approved revision.")
            rev_id = k["approved_rev"]
        else:
            kind, rev_id = "preview", k["current_rev"]
        return self.jobs.enqueue("render", clip_id=kid, source_id=k["source_id"],
                                 payload={"rev": rev_id, "kind": kind}, dedupe_key=f"render:{kid}:{rev_id}:{kind}",
                                 message=f"{kind} render queued")

    def _render(self, kid: str, rev_id: int, kind: str, ctx: JobContext | None) -> Path:
        k = self.db.one("SELECT * FROM clips WHERE id=?", [kid])
        rev = self.db.one("SELECT * FROM revisions WHERE id=? AND clip_id=?", [rev_id, kid])
        src = self.db.one("SELECT * FROM sources WHERE id=?", [k["source_id"]])
        key = render_key(src["id"], rev, kind, self.settings)
        existing = self.db.one("SELECT * FROM renders WHERE key=? AND status='done'", [key])
        if existing and Path(existing["path"]).exists():
            return Path(existing["path"])
        out = self.root / "renders" / kid / f"r{rev_id}_{kind}_{key}.mp4"
        render_clip(Path(src["media_path"]), bool(src["has_audio"]), rev, out, kind=kind, settings=self.settings,
                    on_progress=(lambda f: ctx.progress(f, f"rendering {kind} {int(f * 100)}%", "render")) if ctx else None,
                    cancel=ctx.cancel if ctx else None)
        self.db.exec("INSERT OR REPLACE INTO renders (id,clip_id,rev,kind,key,path,status,created) VALUES (?,?,?,?,?,?,?,?)",
                     [new_id("rnd"), kid, rev_id, kind, key, str(out), "done", now()])
        return out

    def _job_render(self, ctx: JobContext) -> dict:
        p = ctx.job["payload"]
        out = self._render(ctx.job["clip_id"], p["rev"], p["kind"], ctx)
        return {"message": f"{p['kind']} ready", "rev": p["rev"], "kind": p["kind"], "file": out.name}

    def render_file(self, kid: str, kind: str, rev: int | None = None) -> Path | None:
        k = self.db.one("SELECT * FROM clips WHERE id=?", [check_id(kid)])
        if not k:
            return None
        rev = rev or (k["approved_rev"] if kind == "final" else k["current_rev"])
        r = self.db.one("SELECT path FROM renders WHERE clip_id=? AND rev=? AND kind=? AND status='done' "
                        "ORDER BY created DESC", [kid, rev, kind])
        return Path(r["path"]) if r and Path(r["path"]).exists() else None

    # ================================================================ export
    def request_export(self, clip_ids: list[str] | None = None, source_id: str | None = None) -> dict:
        if clip_ids:
            ids = [check_id(c) for c in clip_ids]
            rows = [self.db.one("SELECT id,status,approved_rev FROM clips WHERE id=?", [c]) for c in ids]
            bad = [r["id"] for r in rows if not r or r["status"] != "approved"]
            if bad:
                raise UserError(f"Only approved clips export; {len(bad)} selected clip(s) are not approved.")
        else:
            rows = self.db.all("SELECT id, approved_rev FROM clips WHERE status='approved'" +
                               (" AND source_id=?" if source_id else "") + " ORDER BY created",
                               [check_id(source_id)] if source_id else [])
            ids = [r["id"] for r in rows]
        if not ids:
            raise UserError("No approved clips to export yet.")
        from .render import RENDER_VERSION
        key = hashlib.sha1(json.dumps([RENDER_VERSION, [[r["id"], r["approved_rev"]] for r in rows]]).encode()).hexdigest()[:10]
        return self.jobs.enqueue("export", payload={"clip_ids": ids, "key": key}, dedupe_key=f"export:{key}",
                                 message=f"export of {len(ids)} clip(s) queued")

    def _job_export(self, ctx: JobContext) -> dict:
        ids, key = ctx.job["payload"]["clip_ids"], ctx.job["payload"]["key"]
        zpath = self.root / "exports" / f"clipdesk_{key}.zip"
        if zpath.exists():
            return {"message": "export ready (unchanged since last export)", "zip": zpath.name, "clips": len(ids)}
        manifest = []
        n = len(ids)
        for i, kid in enumerate(ids):
            k = self.clip(kid)
            rev = k["approved"]
            base = i / n

            class _Ctx:  # scale render progress into this export's progress
                cancel = ctx.cancel

                @staticmethod
                def progress(f, m=None, st=None, force=False):
                    ctx.progress(base + f / n, f"clip {i + 1}/{n}: {m or ''}", "render", force)
            path = self._render(kid, rev["id"], "final", _Ctx)
            manifest.append((k, rev, path))
        tmp = zpath.with_suffix(".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            rows = []
            for idx, (k, rev, path) in enumerate(manifest, 1):
                slug = re.sub(r"[^a-z0-9]+", "-", (rev["title"] or k["id"]).lower()).strip("-")[:40] or k["id"]
                stem = f"{idx:02d}_{slug}"
                local = to_local(rev["captions"], rev["start"], rev["end"])
                off = float(k["time_offset"] or 0)
                z.write(path, f"{stem}.mp4", compress_type=zipfile.ZIP_STORED)
                if local:  # no caption files for clips without captions
                    z.writestr(f"{stem}.srt", build_srt(local))
                    z.write(path.with_suffix(".ass"), f"{stem}.ass")
                info = {"clip_id": k["id"], "revision": rev["id"], "title": rev["title"], "hashtags": rev["hashtags"],
                        "creator": k["creator_name"], "source_title": k["source_title"],
                        "source_start": round(rev["start"] + off, 3), "source_end": round(rev["end"] + off, 3),
                        "source_start_tc": detect.fmt_tc(rev["start"] + off, True),
                        "source_end_tc": detect.fmt_tc(rev["end"] + off, True), "captions": len(local),
                        "duration": round(rev["end"] - rev["start"], 3), "file": f"{stem}.mp4"}
                z.writestr(f"{stem}.txt", f"{rev['title']}\n\n{' '.join(rev['hashtags'])}\n\n"
                                          f"Source: {k['source_title']} ({k['creator_name'] or 'no creator'})\n"
                                          f"Source time: {info['source_start_tc']} – {info['source_end_tc']}\n"
                                          f"Captions: {len(local)} line(s){'' if local else ' — none (no transcript/captions)'}\n")
                rows.append(info)
            z.writestr("manifest.json", json.dumps(rows, indent=2))
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({**r, "hashtags": " ".join(r["hashtags"])})
            z.writestr("manifest.csv", buf.getvalue())
        tmp.replace(zpath)
        return {"message": f"exported {n} clip(s)", "zip": zpath.name, "clips": n}

    # ================================================================ system
    def system(self) -> dict:
        import platform
        from .render import face_model_available
        provider, label = score.resolve_provider(self.settings)
        return {
            "ffmpeg": media.ffmpeg_bin(), "ffprobe": media.ffprobe_bin(),
            "yt_dlp": ingest.ytdlp_available(), "faster_whisper": transcribe.whisper_available(),
            "whisper_model": self.settings.whisper_model,
            "whisper_model_cached": transcribe.whisper_model_cached(self.settings.whisper_model),
            "face_model": face_model_available(), "ranker": label, "provider": provider,
            "data_dir": str(self.root), "host": platform.node(), "os": platform.platform(),
            "limits": {"max_clip_s": CFG.max_clip_s, "min_clip_s": CFG.min_clip_s, "out": f"{CFG.out_w}x{CFG.out_h}",
                       "fps": CFG.fps},
            "settings": self.settings.__dict__,
        }

    def jobs_list(self, active_only=False) -> list[dict]:
        if active_only:
            return self.db.all("SELECT * FROM jobs WHERE status IN ('queued','running','cancelling') ORDER BY created")
        return self.db.all("SELECT * FROM jobs ORDER BY updated DESC LIMIT 50")


def _clean_ytdlp(msg: str) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    msg = msg.replace("ERROR: ", "").strip()
    return msg[:400]
