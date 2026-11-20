"""Source import: URL validation, yt-dlp replay import, local uploads, chat logs."""
from __future__ import annotations

import csv
import io
import json
import re
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, parse_qs

YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
TW_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"}


class SourceURLError(ValueError):
    pass


def validate_url(raw: str) -> dict:
    """Return {'origin','url','kind','id'} or raise SourceURLError with a user-facing message.

    kind: 'video' (importable replay/VOD/clip) or 'channel' (a live channel page, no replay).
    """
    s = (raw or "").strip()
    if not s:
        raise SourceURLError("Paste a YouTube or Twitch recording URL.")
    if len(s) > 500 or any(c.isspace() for c in s):
        raise SourceURLError("That doesn't look like a single URL.")
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    if u.scheme.lower() not in ("http", "https"):
        raise SourceURLError("Only http(s) links are supported.")
    host = (u.hostname or "").lower()
    if u.username or u.password or u.port not in (None, 80, 443):
        raise SourceURLError("URLs with credentials or custom ports are not accepted.")
    parts = [p for p in u.path.split("/") if p]
    if host in YT_HOSTS:
        vid = None
        if host == "youtu.be" and parts:
            vid = parts[0]
        elif parts[:1] == ["watch"]:
            vid = (parse_qs(u.query).get("v") or [None])[0]
        elif len(parts) >= 2 and parts[0] in ("live", "shorts", "embed", "v"):
            vid = parts[1]
        if not vid or not YT_ID.match(vid):
            raise SourceURLError("YouTube link must point to one video, e.g. youtube.com/watch?v=… or youtube.com/live/….")
        return {"origin": "youtube", "kind": "video", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
    if host in TW_HOSTS:
        if host == "clips.twitch.tv" and parts:
            return {"origin": "twitch", "kind": "video", "id": parts[0], "url": f"https://clips.twitch.tv/{parts[0]}"}
        if len(parts) >= 2 and parts[0] == "videos" and parts[1].isdigit():
            return {"origin": "twitch", "kind": "video", "id": parts[1], "url": f"https://www.twitch.tv/videos/{parts[1]}"}
        if len(parts) >= 3 and parts[1] == "clip":
            return {"origin": "twitch", "kind": "video", "id": parts[2], "url": f"https://www.twitch.tv/{parts[0]}/clip/{parts[2]}"}
        if len(parts) == 1 and re.match(r"^[A-Za-z0-9_]{3,25}$", parts[0]):
            return {"origin": "twitch", "kind": "channel", "id": parts[0], "url": f"https://www.twitch.tv/{parts[0]}"}
        raise SourceURLError("Twitch link must be a VOD (twitch.tv/videos/…) or a clip.")
    raise SourceURLError("Only YouTube and Twitch recordings are supported. For anything else, upload the file.")


# ---------------------------------------------------------------- yt-dlp
LIVE_WAITING_MARKERS = ("this live event will begin", "premieres in", "is_upcoming", "is_live",
                        "live stream recording is not available", "post_live", "will begin in")
INACCESSIBLE_MARKERS = ("private video", "members-only", "join this channel", "sign in to confirm",
                        "video unavailable", "this video is unavailable", "has been removed",
                        "not available in your country", "account associated", "http error 403",
                        "http error 404", "does not exist", "subscriber-only", "unable to find video")


def classify_ytdlp_error(msg: str) -> str:
    m = (msg or "").lower()
    if any(k in m for k in LIVE_WAITING_MARKERS):
        return "live_waiting"
    if any(k in m for k in INACCESSIBLE_MARKERS):
        return "inaccessible"
    return "failed"


def classify_info(info: dict) -> tuple[str, str]:
    """Map yt-dlp metadata to a source status before downloading."""
    live = (info.get("live_status") or "").lower()
    if live in ("is_live", "is_upcoming"):
        return "live_waiting", ("Stream is live now — ClipDesk imports the replay once it ends."
                                if live == "is_live" else "Stream hasn't started yet.")
    if live == "post_live":
        return "live_waiting", "Stream ended; the platform is still processing the replay. Retry later."
    if info.get("availability") in ("private", "premium_only", "subscriber_only", "needs_auth"):
        return "inaccessible", f"Video availability is '{info.get('availability')}' — ClipDesk can't access it."
    return "ready_to_download", ""


def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def ytdlp_probe(url: str) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(url, download=False)


def ytdlp_download(url: str, dest_dir: Path, *, section: tuple[float, float] | None,
                   on_progress: Callable[[float, str], None], cancel: threading.Event) -> dict:
    """Download a replay (≤1080p, merged to mp4) plus chat replay / auto-captions when offered."""
    import yt_dlp

    def hook(d):
        if cancel.is_set():
            raise yt_dlp.utils.DownloadError("cancelled by user")
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes", 0) / tot) if tot else 0.0
            on_progress(frac, f"downloading {d.get('_percent_str', '').strip()}")

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "original.%(ext)s"),
        "progress_hooks": [hook],
        "writesubtitles": True, "writeautomaticsub": True,
        "subtitleslangs": ["live_chat", "rechat", "en", "en-orig"], "subtitlesformat": "json3/vtt/json/best",
        "ignoreerrors": False, "retries": 3,
    }
    if section:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(None, [section])
        opts["force_keyframes_at_cuts"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return info


# ---------------------------------------------------------------- chat logs
def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hms(v: str) -> float | None:
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$", str(v).strip())
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + float(s)


def parse_chat(data: bytes, name: str = "") -> list[dict]:
    """Normalise a chat log to [{'t': seconds, 'text': str}] sorted by time.

    Accepts: ClipDesk JSON ([{t,text}] or {"messages": [...]}), CSV with a time column,
    YouTube live_chat replay (JSON lines from yt-dlp), Twitch rechat JSON (yt-dlp).
    """
    text = data.decode("utf-8", errors="replace").strip()
    out: list[dict] = []
    if not text:
        return out
    if text[0] in "[{":
        # YouTube live_chat replay is JSON-lines
        if "replayChatItemAction" in text[:5000]:
            for line in text.splitlines():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                r = obj.get("replayChatItemAction", {})
                t = _num(r.get("videoOffsetTimeMsec"))
                for act in r.get("actions", []):
                    item = act.get("addChatItemAction", {}).get("item", {})
                    rend = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                    if not rend or t is None:
                        continue
                    runs = rend.get("message", {}).get("runs", [])
                    msg = "".join(x.get("text") or ":" + (x.get("emoji", {}).get("shortcuts") or ["emote"])[0].strip(":") + ":"
                                  for x in runs)
                    out.append({"t": t / 1000.0, "text": msg})
            return sorted(out, key=lambda m: m["t"])
        obj = json.loads(text)
        rows = obj
        if isinstance(obj, dict):
            rows = obj.get("messages") or obj.get("comments") or obj.get("chat") or []
        for r in rows:
            if not isinstance(r, dict):
                continue
            t = None
            for k in ("t", "time", "offset", "seconds", "content_offset_seconds", "timestamp"):
                if k in r:
                    t = _num(r[k]) if _num(r[k]) is not None else _hms(r[k])
                    break
            msg = r.get("text") or r.get("message") or r.get("body") or ""
            if isinstance(msg, dict):  # twitch rechat
                msg = msg.get("body") or " ".join(f.get("text", "") for f in msg.get("fragments", []))
            if t is not None:
                out.append({"t": float(t), "text": str(msg)})
        return sorted(out, key=lambda m: m["t"])
    # CSV
    reader = csv.DictReader(io.StringIO(text))
    fields = [f.lower() for f in (reader.fieldnames or [])]
    tcol = next((f for f in (reader.fieldnames or []) if f.lower() in ("t", "time", "offset", "seconds", "timestamp")), None)
    mcol = next((f for f in (reader.fieldnames or []) if f.lower() in ("text", "message", "msg", "body")), None)
    if not tcol:
        raise ValueError(f"Chat CSV needs a time column (t/time/offset/seconds); found {fields}")
    for r in reader:
        t = _num(r[tcol]) if _num(r[tcol]) is not None else _hms(r[tcol])
        if t is not None:
            out.append({"t": t, "text": r.get(mcol, "") if mcol else ""})
    return sorted(out, key=lambda m: m["t"])
