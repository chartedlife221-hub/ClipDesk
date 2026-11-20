"""HTTP API + static UI.

Routes are plain Starlette routes. When FastAPI is installed (requirements.txt) the app is a
FastAPI instance (FastAPI subclasses Starlette and accepts these routes unchanged); otherwise a
bare Starlette app is used. Both bind to 127.0.0.1 by default (see run.py).
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .ingest import SourceURLError
from .service import App, UserError, check_id

log = logging.getLogger("clipdesk.server")
STATIC = Path(__file__).parent / "static"


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


def create_app(svc: App | None = None, start_jobs: bool = True):
    svc = svc or App()

    def handler(fn):
        async def endpoint(request: Request):
            try:
                body = {}
                if request.method in ("POST", "PUT", "PATCH") and \
                        request.headers.get("content-type", "").startswith("application/json"):
                    raw = await request.body()
                    body = json.loads(raw) if raw else {}
                res = await run_in_threadpool(fn, request, body)
                if isinstance(res, Response):
                    return res
                return JSONResponse(res)
            except (UserError, SourceURLError) as e:
                return _err(str(e), 400)
            except json.JSONDecodeError:
                return _err("Request body is not valid JSON.")
            except Exception as e:  # noqa: BLE001
                log.exception("unhandled error on %s", request.url.path)
                return _err(f"Internal error: {type(e).__name__}: {str(e)[:200]}", 500)
        return endpoint

    def p(request: Request, name: str) -> str:
        return check_id(request.path_params[name])

    # ---------------------------------------------------------------- system
    def health(req, body):
        s = svc.system()
        ok = bool(s["ffmpeg"] and s["ffprobe"])
        return JSONResponse({"ok": ok, "version": __version__, "ffmpeg": bool(s["ffmpeg"]),
                             "jobs_running": len(svc.jobs.active)}, status_code=200 if ok else 503)

    def system(req, body):
        return svc.system()

    def put_settings(req, body):
        s = svc.settings
        for k in ("whisper_model", "model_provider", "anthropic_model", "openai_model", "default_layout",
                  "caption_font", "caption_size"):
            if k in body:
                setattr(s, k, type(getattr(s, k))(body[k]))
        if s.model_provider not in ("auto", "anthropic", "openai", "local"):
            s.model_provider = "auto"
        s.save()
        return svc.system()

    # ---------------------------------------------------------------- creators
    def creators(req, body):
        if req.method == "POST":
            return svc.add_creator(body.get("name", ""), body.get("kind", "streamer"), body.get("notes", ""))
        return svc.creators()

    # ---------------------------------------------------------------- sources
    def sources(req, body):
        cid = req.query_params.get("creator")
        return svc.sources(check_id(cid) if cid else None)

    def source_url(req, body):
        sec = None
        if body.get("section_start") not in (None, "") and body.get("section_end") not in (None, ""):
            sec = (float(body["section_start"]), float(body["section_end"]))
        return svc.add_url_source(body.get("url", ""), body.get("creator_id") or None,
                                  body.get("content_kind", "auto"), sec)

    async def source_upload(request: Request):
        try:
            form = await request.form(max_files=1, max_fields=10)
            f = form.get("file")
            if f is None or not hasattr(f, "filename"):
                return _err("Choose a video file to upload.")
            res = await run_in_threadpool(svc.add_upload_source, f.filename, f.file,
                                          form.get("creator_id") or None, form.get("content_kind") or "auto")
            return JSONResponse(res)
        except UserError as e:
            return _err(str(e))

    def source_one(req, body):
        sid = p(req, "sid")
        if req.method == "PATCH":
            return svc.update_source(sid, body)
        s = svc.source(sid)
        s["requests"] = svc.requests(sid)
        s["transcript_available"] = s["transcript_status"] == "ready"
        return s

    async def source_attach(request: Request):
        sid = check_id(request.path_params["sid"])
        what = request.path_params["what"]
        try:
            form = await request.form(max_files=1)
            f = form.get("file")
            if f is None or not hasattr(f, "filename"):
                return _err("Choose a file.")
            data = await f.read()
            if len(data) > 200 * 1024 * 1024:
                return _err("File too large (200 MB max).")
            fn = svc.attach_chat if what == "chat" else svc.attach_transcript
            return JSONResponse(await run_in_threadpool(fn, sid, data, f.filename or ""))
        except UserError as e:
            return _err(str(e))

    def source_transcribe(req, body):
        return svc.request_transcription(p(req, "sid"))

    def source_retry(req, body):
        return svc.retry_source(p(req, "sid"))

    def source_media(req, body):
        return FileResponse(svc.media_path(p(req, "sid")), media_type="video/mp4")

    def source_poster(req, body):
        r = svc.db.one("SELECT poster_path FROM sources WHERE id=?", [p(req, "sid")])
        if not r or not r["poster_path"] or not Path(r["poster_path"]).exists():
            return Response(status_code=404)
        return FileResponse(r["poster_path"], media_type="image/jpeg")

    def source_transcript(req, body):
        tr = svc.transcript(p(req, "sid"))
        return tr or {"words": [], "segments": []}

    def source_signals(req, body):
        return svc.signals(p(req, "sid"))

    def source_find(req, body):
        return svc.find(p(req, "sid"), body.get("text", ""), body.get("mode", "auto"))

    def request_one(req, body):
        return svc.request(p(req, "rid"))

    # ---------------------------------------------------------------- clips
    def clips(req, body):
        if req.method == "POST":
            return svc.create_manual_clip(check_id(body.get("source_id", "")), body.get("start"), body.get("end"),
                                          body.get("layout"))
        q = req.query_params
        return svc.clips(q.get("source") or None, q.get("status") or None, q.get("creator") or None)

    def clip_one(req, body):
        k = svc.clip(p(req, "kid"))
        k["history"] = svc.history(k["id"])[:20]
        return k

    def clip_revise(req, body):
        return svc.revise(p(req, "kid"), body)

    def clip_status(req, body):
        return svc.set_status(p(req, "kid"), body.get("status", ""))

    def clip_undo(req, body):
        return svc.undo(p(req, "kid"))

    def clip_autocenter(req, body):
        return svc.auto_center(p(req, "kid"))

    def clip_render(req, body):
        return svc.request_render(p(req, "kid"), body.get("kind", "preview"))

    def clip_file(req, body):
        kid = p(req, "kid")
        kind = req.path_params["kind"]
        path = svc.render_file(kid, "final" if kind == "download" else "preview")
        if not path:
            return _err("Not rendered yet.", 404)
        if kind == "download":
            k = svc.clip(kid)
            import re
            name = (re.sub(r"[^A-Za-z0-9]+", "-", k["approved"]["title"] or kid).strip("-")[:50] or kid) + ".mp4"
            return FileResponse(path, media_type="video/mp4", filename=name)
        return FileResponse(path, media_type="video/mp4")

    def export(req, body):
        return svc.request_export(body.get("clip_ids") or None, body.get("source_id") or None)

    def export_file(req, body):
        import re
        name = req.path_params["name"]
        if not re.match(r"^clipdesk_[0-9a-f]{10}\.zip$", name):
            return _err("bad name", 404)
        path = svc.root / "exports" / name
        if not path.exists():
            return _err("not found", 404)
        return FileResponse(path, media_type="application/zip", filename=name)

    # ---------------------------------------------------------------- jobs
    def jobs(req, body):
        return svc.jobs_list(req.query_params.get("active") == "1")

    def job_one(req, body):
        j = svc.db.one("SELECT * FROM jobs WHERE id=?", [p(req, "jid")])
        return j or _err("not found", 404)

    def job_cancel(req, body):
        return svc.jobs.cancel(p(req, "jid")) or _err("not found", 404)

    def job_retry(req, body):
        return svc.jobs.retry(p(req, "jid")) or _err("not found", 404)

    def index(req, body):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    H = handler
    routes = [
        Route("/", H(index)),
        Route("/api/health", H(health)),
        Route("/api/system", H(system)),
        Route("/api/settings", H(put_settings), methods=["PUT"]),
        Route("/api/creators", H(creators), methods=["GET", "POST"]),
        Route("/api/sources", H(sources)),
        Route("/api/sources/url", H(source_url), methods=["POST"]),
        Route("/api/sources/upload", source_upload, methods=["POST"]),
        Route("/api/sources/{sid}", H(source_one), methods=["GET", "PATCH"]),
        Route("/api/sources/{sid}/attach/{what:str}", source_attach, methods=["POST"]),
        Route("/api/sources/{sid}/transcribe", H(source_transcribe), methods=["POST"]),
        Route("/api/sources/{sid}/retry", H(source_retry), methods=["POST"]),
        Route("/api/sources/{sid}/media", H(source_media)),
        Route("/api/sources/{sid}/poster", H(source_poster)),
        Route("/api/sources/{sid}/transcript", H(source_transcript)),
        Route("/api/sources/{sid}/signals", H(source_signals)),
        Route("/api/sources/{sid}/find", H(source_find), methods=["POST"]),
        Route("/api/requests/{rid}", H(request_one)),
        Route("/api/clips", H(clips), methods=["GET", "POST"]),
        Route("/api/clips/{kid}", H(clip_one)),
        Route("/api/clips/{kid}/revise", H(clip_revise), methods=["POST"]),
        Route("/api/clips/{kid}/status", H(clip_status), methods=["POST"]),
        Route("/api/clips/{kid}/undo", H(clip_undo), methods=["POST"]),
        Route("/api/clips/{kid}/autocenter", H(clip_autocenter), methods=["POST"]),
        Route("/api/clips/{kid}/render", H(clip_render), methods=["POST"]),
        Route("/api/clips/{kid}/{kind:str}.mp4", H(clip_file)),
        Route("/api/export", H(export), methods=["POST"]),
        Route("/api/exports/{name:str}", H(export_file)),
        Route("/api/jobs", H(jobs)),
        Route("/api/jobs/{jid}", H(job_one)),
        Route("/api/jobs/{jid}/cancel", H(job_cancel), methods=["POST"]),
        Route("/api/jobs/{jid}/retry", H(job_retry), methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC)), name="static"),
    ]

    @asynccontextmanager
    async def lifespan(app):
        if start_jobs:
            svc.jobs.start()
        yield
        svc.jobs.stop()

    try:
        from fastapi import FastAPI
        app = FastAPI(title="ClipDesk", version=__version__, routes=routes, lifespan=lifespan,
                      docs_url=None, redoc_url=None)
    except ImportError:
        from starlette.applications import Starlette
        app = Starlette(routes=routes, lifespan=lifespan)
    app.state.svc = svc
    return app
