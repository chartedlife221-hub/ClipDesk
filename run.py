#!/usr/bin/env python3
"""ClipDesk launcher — the single documented start command:

    python run.py                 start on 127.0.0.1 (first free port from 8765), open the browser
    python run.py --no-browser    start without opening a browser
    python run.py --port 9000     prefer a specific port
    python run.py --stop          stop a ClipDesk started from this folder
    python run.py --check         check dependencies only

Stop: Ctrl+C in this window (or `python run.py --stop`). Restart: run the start command again;
approved clips, edits and finished renders are kept in clipdesk-data/.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def check_deps() -> tuple[list[str], list[str]]:
    errors, notes = [], []
    if sys.version_info < (3, 10):
        errors.append(f"Python 3.10+ required (found {platform.python_version()})")
    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            errors.append(f"{exe} not found on PATH — install FFmpeg (see README) and reopen the terminal")
    for mod, why in (("starlette", "web server"), ("uvicorn", "web server"), ("numpy", "signals"),
                     ("multipart", "uploads (python-multipart)")):
        try:
            __import__(mod)
        except ImportError:
            errors.append(f"Python package for {why} missing: run  pip install -r requirements.txt")
    for mod, msg in (("fastapi", "fastapi not installed — running on plain Starlette (fine)"),
                     ("yt_dlp", "yt-dlp not installed — URL import disabled, file upload works"),
                     ("faster_whisper", "faster-whisper not installed — speech search needs an uploaded transcript"),
                     ("cv2", "opencv not installed — speaker auto-centre disabled (manual position works)")):
        try:
            __import__(mod)
        except ImportError:
            notes.append(msg)
    try:
        import faster_whisper  # noqa: F401
        from clipdesk.config import Settings
        from clipdesk.transcribe import whisper_model_cached
        m = Settings.load().whisper_model
        if not whisper_model_cached(m):
            notes.append(f"first transcription downloads the Whisper '{m}' model "
                         f"(~{ {'tiny': 75, 'base': 145, 'small': 490, 'medium': 1500}.get(m, '?') } MB) into clipdesk-data/models")
    except ImportError:
        pass
    return errors, notes


def free_port(host: str, start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name != "nt":  # match uvicorn: a port in TIME_WAIT after a restart is still usable
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {start} and {start + 49}")


def pidfile() -> Path:
    from clipdesk.config import data_root
    return data_root() / "clipdesk.pid"


def stop() -> None:
    p = pidfile()
    if not p.exists():
        print("ClipDesk is not running (no pid file).")
        return
    info = json.loads(p.read_text())
    try:
        os.kill(info["pid"], signal.SIGINT if os.name != "nt" else signal.SIGTERM)
        print(f"Stopped ClipDesk (pid {info['pid']}, {info['url']}).")
    except (ProcessLookupError, PermissionError, OSError):
        print("ClipDesk process was not running; removed stale pid file.")
    p.unlink(missing_ok=True)


def setup_logging():
    from clipdesk.config import data_root
    log = data_root() / "logs" / "clipdesk.log"
    h = RotatingFileHandler(log, maxBytes=5_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h, logging.StreamHandler()])
    return log


def main():
    ap = argparse.ArgumentParser(description="ClipDesk local server")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default loopback only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.stop:
        return stop()
    errors, notes = check_deps()
    print("ClipDesk dependency check")
    for e in errors:
        print("  ✗", e)
    for n in notes:
        print("  •", n)
    if errors:
        raise SystemExit(1)
    print("  ✓ required dependencies found")
    if a.check:
        return
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ! binding to {a.host}: ClipDesk has no login — anyone who can reach this address can use it.")
    port = free_port(a.host, a.port)
    logfile = setup_logging()
    import uvicorn
    from clipdesk.server import create_app
    url = f"http://{'127.0.0.1' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{port}/"
    app = create_app()

    def announce():
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "api/health", timeout=1) as r:
                    h = json.loads(r.read())
                    if h.get("ok"):
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            print("  ✗ server did not answer /api/health — see", logfile)
            return
        pidfile().write_text(json.dumps({"pid": os.getpid(), "url": url}))
        print(f"\n  ClipDesk is running → {url}\n  host: {platform.node()} ({platform.system()})"
              f"\n  data: {app.state.svc.root}\n  log:  {logfile}"
              f"\n  stop: Ctrl+C here, or `python run.py --stop`   restart: `python run.py`\n")
        if not a.no_browser:
            webbrowser.open(url)

    threading.Thread(target=announce, daemon=True).start()
    try:
        uvicorn.run(app, host=a.host, port=port, log_level="warning")
    finally:
        try:
            pidfile().unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
