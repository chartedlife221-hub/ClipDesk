"""Brand kits: a logo and default caption/logo style per creator, plus a general ("global") kit.

Stored under clipdesk-data/brand/:
    <owner>.png    the logo, re-encoded by ffmpeg (≤ 800 px wide, alpha kept) — never the uploaded bytes
    <owner>.json   {"defaults": {"style": {...}, "logo": {...}, "caption_size": int}}
<owner> is a creator id or "global". A creator's kit wins over the global one.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import media
from .config import data_root

OWNER_RE = re.compile(r"^(global|cr_[0-9a-f]{10})$")
DEFAULT_LOGO = {"on": False, "pos": "tr", "size": 0.18, "opacity": 0.9}
POSITIONS = {"tl", "tc", "tr", "bl", "br"}
MAX_UPLOAD = 5 * 1024 * 1024


class BrandError(ValueError):
    pass


def _dir() -> Path:
    d = data_root() / "brand"
    d.mkdir(exist_ok=True)
    return d


def check_owner(owner: str) -> str:
    if not isinstance(owner, str) or not OWNER_RE.match(owner):
        raise BrandError("Unknown brand owner.")
    return owner


def logo_path(owner: str) -> Path | None:
    p = _dir() / f"{check_owner(owner)}.png"
    return p if p.exists() else None


def get(owner: str) -> dict:
    p = _dir() / f"{check_owner(owner)}.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    return {"owner": owner, "has_logo": logo_path(owner) is not None, "defaults": data.get("defaults") or {}}


def clean_logo_settings(v: dict | None) -> dict:
    v = {**DEFAULT_LOGO, **(v or {})}
    return {"on": bool(v["on"]), "pos": v["pos"] if v["pos"] in POSITIONS else "tr",
            "size": max(0.06, min(0.5, float(v["size"]))), "opacity": max(0.2, min(1.0, float(v["opacity"])))}


def save_defaults(owner: str, defaults: dict) -> dict:
    from .captions import DEFAULT_STYLE, PRESETS
    st = {**DEFAULT_STYLE, **(defaults.get("style") or {})}
    if st["preset"] not in PRESETS:
        raise BrandError("Unknown caption style.")
    if not re.match(r"^#[0-9A-Fa-f]{6}$", str(st.get("color", ""))):
        raise BrandError("Colour must look like #FFE14D.")
    clean = {"style": {"preset": st["preset"], "color": st["color"].upper(),
                       "words": None if st.get("words") in (None, "") else max(0, min(8, int(st["words"]))),
                       "upper": None if st.get("upper") is None else bool(st["upper"])},
             "logo": clean_logo_settings(defaults.get("logo"))}
    if defaults.get("caption_size"):
        clean["caption_size"] = max(40, min(120, int(defaults["caption_size"])))
    p = _dir() / f"{check_owner(owner)}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"defaults": clean}, indent=2))
    tmp.replace(p)
    return get(owner)


def save_logo(owner: str, data: bytes, filename: str) -> dict:
    check_owner(owner)
    if len(data) > MAX_UPLOAD:
        raise BrandError("Logo is too large (5 MB max).")
    if not data:
        raise BrandError("The file is empty.")
    d = _dir()
    raw = d / f".{owner}.upload"
    raw.write_bytes(data)
    try:
        info = media.probe_image(raw)
        if info["codec"] not in ("png", "mjpeg", "webp", "bmp", "gif", "tiff"):
            raise BrandError("Upload a PNG, JPG or WebP image.")
        out = d / f".{owner}.new.png"
        media.run_ffmpeg(["-i", str(raw), "-frames:v", "1", "-vf", "scale='min(800,iw)':-2,format=rgba", str(out)])
        out.replace(d / f"{owner}.png")
    except media.MediaError as e:
        raise BrandError("That file isn't an image ClipDesk can read.") from e
    finally:
        raw.unlink(missing_ok=True)
    return get(owner)


def remove_logo(owner: str) -> dict:
    p = logo_path(owner)
    if p:
        p.unlink()
    return get(owner)


def owner_for(creator_id: str | None) -> str:
    return creator_id or "global"


def kit_for(creator_id: str | None) -> tuple[dict, Path | None]:
    """Defaults and logo that apply to a creator's clips (creator kit, else the global kit)."""
    own = get(owner_for(creator_id)) if creator_id else {"defaults": {}, "has_logo": False}
    glob = get("global")
    defaults = own["defaults"] or glob["defaults"]
    logo = logo_path(creator_id) if creator_id and own["has_logo"] else logo_path("global")
    return defaults, logo
