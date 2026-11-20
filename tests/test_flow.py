"""End-to-end through the service layer and the real HTTP server (needs ffmpeg)."""
import io
import json
import socket
import subprocess
import threading
import time
import zipfile

import pytest
import requests

from clipdesk.db import DB
from clipdesk.service import App, UserError


def ffprobe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate"
                          ":format=duration", "-of", "json", str(p)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def wait_source(app, sid, t=120):
    for _ in range(t * 5):
        s = app.source(sid)
        if s["status"] in ("ready", "failed", "inaccessible", "live_waiting"):
            return s
        time.sleep(0.2)
    raise TimeoutError


def test_full_service_flow(data_dir, tiny_video):
    app = App()
    app.jobs.start()
    try:
        cr = app.add_creator("Test Streamer", "streamer")
        with open(tiny_video, "rb") as f:
            s = app.add_upload_source("tiny.mp4", f, cr["id"], "gaming")
        s = wait_source(app, s["id"])
        assert s["status"] == "ready" and abs(s["duration"] - 12) < 0.2
        # manual clip validation
        with pytest.raises(UserError):
            app.create_manual_clip(s["id"], 8, 4)
        with pytest.raises(UserError):
            app.create_manual_clip(s["id"], 5, 13)
        k = app.create_manual_clip(s["id"], 4.0, 10.0)
        k = app.revise(k["id"], {"start": 5.0, "end": 9.5, "captions": [{"s": 5.2, "e": 7.0, "text": "EDITED LINE"}]})
        assert k["rev"]["start"] == 5.0 and k["can_undo"]
        k = app.set_status(k["id"], "approved")
        assert k["approved_rev"] == k["current_rev"]
        k2 = app.revise(k["id"], {"title": "later edit"})
        assert k2["edited_since_approval"]
        k3 = app.undo(k["id"])
        assert not k3["edited_since_approval"] and k3["rev"]["title"] != "later edit"
        # discovery on the loud burst while a render is queued
        req = app.find(s["id"], "find the loud reaction", "gaming")
        j = app.request_render(k["id"], "final")
        assert app.jobs.wait(j["id"], 300)["status"] == "done"
        r = app.request(req["id"])
        assert app.jobs.wait(r["job"]["id"], 120)["status"] == "done"
        r = app.request(req["id"])
        assert r["note"]
        out = app.render_file(k["id"], "final")
        info = ffprobe(out)
        v = next(x for x in info["streams"] if x["codec_type"] == "video")
        a = next(x for x in info["streams"] if x["codec_type"] == "audio")
        assert (v["codec_name"], v["width"], v["height"], v["r_frame_rate"]) == ("h264", 1080, 1920, "30/1")
        assert a["codec_name"] == "aac"
        assert abs(float(info["format"]["duration"]) - 4.5) < 0.1
        ass = out.with_suffix(".ass").read_text()
        assert "Dialogue: 0,0:00:00.20,0:00:02.00" in ass and "EDITED LINE" in ass  # 5.2–7.0 → 0.2–2.0 local
        # export zip
        ej = app.request_export()
        ej = app.jobs.wait(ej["id"], 300)
        assert ej["status"] == "done"
        z = zipfile.ZipFile(app.root / "exports" / ej["result"]["zip"])
        names = z.namelist()
        assert any(n.endswith(".mp4") for n in names) and "manifest.json" in names and any(n.endswith(".srt") for n in names)
        man = json.loads(z.read("manifest.json"))
        assert man[0]["source_start"] == 5.0 and man[0]["source_end"] == 9.5
        renders_before = sorted(p.name for p in (app.root / "renders").rglob("*.mp4"))
    finally:
        app.jobs.stop()
    # "restart": new App over the same data dir
    app2 = App(DB())
    app2.jobs.start()
    try:
        k = app2.clip(k["id"])
        assert k["status"] == "approved"
        ej2 = app2.jobs.wait(app2.request_export()["id"], 120)
        assert ej2["result"]["zip"] == ej["result"]["zip"]  # same approved set → same bundle, no duplicate
        assert sorted(p.name for p in (app2.root / "renders").rglob("*.mp4")) == renders_before
    finally:
        app2.jobs.stop()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def server(data_dir):
    import uvicorn
    from clipdesk.server import create_app
    port = _free_port()
    cfg = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(base + "/api/health", timeout=0.5)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    t.join(5)


def test_http_api(server, tiny_video):
    h = requests.get(server + "/api/health").json()
    assert h["ok"] is True
    r = requests.post(server + "/api/sources/url", json={"url": "https://vimeo.com/1"})
    assert r.status_code == 400 and "YouTube and Twitch" in r.json()["error"]
    r = requests.post(server + "/api/sources/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and "Unsupported file type" in r.json()["error"]
    r = requests.post(server + "/api/sources/upload", files={"file": ("fake.mp4", b"not really a video", "video/mp4")})
    sid = r.json()["id"]
    for _ in range(100):
        s = requests.get(f"{server}/api/sources/{sid}").json()
        if s["status"] in ("failed", "ready"):
            break
        time.sleep(0.1)
    assert s["status"] == "failed" and "ffprobe" in s["status_detail"]
    with open(tiny_video, "rb") as f:
        sid = requests.post(server + "/api/sources/upload", files={"file": ("tiny.mp4", f, "video/mp4")}).json()["id"]
    for _ in range(200):
        s = requests.get(f"{server}/api/sources/{sid}").json()
        if s["status"] == "ready":
            break
        time.sleep(0.1)
    assert "media_path" not in s  # no server paths leak to the browser
    rng = requests.get(f"{server}/api/sources/{sid}/media", headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100
    r = requests.post(server + "/api/clips", json={"source_id": sid, "start": 2, "end": 60})
    assert r.status_code == 400 and "past the end" in r.json()["error"]
    assert requests.get(server + "/api/sources/../../etc/passwd").status_code in (400, 404)
    assert requests.get(server + "/api/exports/..%2Fclipdesk.sqlite").status_code == 404
    assert requests.get(server + "/").status_code == 200
