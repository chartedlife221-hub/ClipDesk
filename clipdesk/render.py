"""Reframe + caption burn-in + mux. One function renders previews and finals so they match.

Layouts (stored on the revision as `layout`):
  speaker  : 9:16 crop window centred on `cx` (0..1 of source width), `zoom` ≥ 1.
             `auto_cx` is filled from face detection when OpenCV's face model is available.
  fit      : whole frame scaled to width, blurred copy of itself behind it.
  gameplay : facecam region on top, action region below; regions are normalised
             {x,y,w,h} rectangles on the source; `split` = facecam share of output height.
Captions : `caption_y` = vertical position of the caption baseline (0 top … 1 bottom).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

from .captions import build_ass, to_local
from .config import CFG, PROJECT_ROOT, Settings
from .media import probe, run_ffmpeg

log = logging.getLogger("clipdesk.render")
FONTS_DIR = Path(__file__).parent / "static" / "fonts"

RENDER_VERSION = 5  # bump when encoder settings change so cached renders are redone

DEFAULT_LAYOUTS = {
    "speaker": {"mode": "speaker", "cx": 0.5, "zoom": 1.0, "caption_y": 0.80},
    "fit": {"mode": "fit", "caption_y": 0.80},
    "gameplay": {"mode": "gameplay", "split": 0.38, "caption_y": 0.46,
                 "face": {"x": 0.70, "y": 0.62, "w": 0.28, "h": 0.36},
                 "action": {"x": 0.18, "y": 0.0, "w": 0.64, "h": 1.0}},
}


def default_layout(mode: str) -> dict:
    return json.loads(json.dumps(DEFAULT_LAYOUTS.get(mode, DEFAULT_LAYOUTS["speaker"])))


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _rect(r: dict, W: int, H: int) -> tuple[int, int, int, int]:
    x = _clamp(float(r.get("x", 0)), 0, 0.98)
    y = _clamp(float(r.get("y", 0)), 0, 0.98)
    w = _clamp(float(r.get("w", 1)), 0.02, 1 - x)
    h = _clamp(float(r.get("h", 1)), 0.02, 1 - y)
    return _even(w * W), _even(h * H), _even(x * W) if x * W + _even(w * W) <= W else W - _even(w * W), \
        _even(y * H) if y * H + _even(h * H) <= H else H - _even(h * H)


def video_graph(layout: dict, W: int, H: int, OW: int, OH: int) -> str:
    """filter_complex producing [vbase] (OW×OH, before captions)."""
    mode = layout.get("mode", "speaker")
    fill = "scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
    if mode == "fit":
        return (f"[0:v]split=2[a][b];"
                f"[a]scale={OW // 8}:{OH // 8}:force_original_aspect_ratio=increase,crop={OW // 8}:{OH // 8},"
                f"boxblur=6:2,scale={OW}:{OH},eq=brightness=-0.12[bg];"
                f"[b]scale={OW}:{OH}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vbase]")
    if mode == "gameplay":
        split = _clamp(float(layout.get("split", 0.38)), 0.15, 0.7)
        fh = _even(OH * split)
        ah = OH - fh
        fw_, fh_, fx, fy = _rect(layout.get("face", {}), W, H)
        aw_, ah_, ax, ay = _rect(layout.get("action", {}), W, H)
        return (f"[0:v]split=2[f][a];"
                f"[f]crop={fw_}:{fh_}:{fx}:{fy},{fill.format(w=OW, h=fh)}[top];"
                f"[a]crop={aw_}:{ah_}:{ax}:{ay},{fill.format(w=OW, h=ah)}[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[vbase]")
    # speaker crop
    zoom = _clamp(float(layout.get("zoom", 1.0)), 1.0, 3.0)
    cx = float(layout.get("cx", layout.get("auto_cx", 0.5)))
    ch = H / zoom
    cw = ch * 9 / 16
    if cw > W:  # source narrower than 9:16 — use full width
        cw = W
        ch = min(H, W * 16 / 9)
    cw, ch = _even(cw), _even(ch)
    x = _clamp(cx * W - cw / 2, 0, W - cw)
    cy = float(layout.get("cy", 0.5))
    y = _clamp(cy * H - ch / 2, 0, H - ch)
    return f"[0:v]crop={cw}:{ch}:{int(x)}:{int(y)},scale={OW}:{OH}:flags=lanczos,setsar=1[vbase]"


def render_key(source_id: str, rev: dict, kind: str, settings: Settings) -> str:
    blob = json.dumps({"src": source_id, "s": rev["start"], "e": rev["end"], "cap": rev["captions"],
                       "lay": rev["layout"], "kind": kind, "font": settings.caption_font,
                       "size": settings.caption_size, "v": RENDER_VERSION}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def render_clip(src: Path, has_audio: bool, rev: dict, out: Path, *, kind: str, settings: Settings,
                on_progress: Callable[[float], None] | None = None,
                cancel: threading.Event | None = None) -> Path:
    """Render [start,end] of `src` per revision to `out` (atomic). kind: 'preview' | 'final'."""
    info = probe(src)
    W, H = info["width"], info["height"]
    OW, OH = (CFG.out_w, CFG.out_h)
    start, end = float(rev["start"]), float(rev["end"])
    dur = end - start
    layout = rev.get("layout") or default_layout("speaker")
    work = out.parent / f".work_{out.stem}"
    work.mkdir(parents=True, exist_ok=True)
    local = to_local(rev.get("captions") or [], start, end)
    cap_y = _clamp(float(layout.get("caption_y", 0.8)), 0.1, 0.97)
    size = int(layout.get("caption_size", settings.caption_size))
    ass = build_ass(local, w=OW, h=OH, font=settings.caption_font, size=size, margin_v=int(OH * (1 - cap_y)))
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    graph = video_graph(layout, W, H, OW, OH)
    fonts = FONTS_DIR.as_posix().replace(":", r"\:")  # Windows drive colon must be escaped in filter args
    tail = f"[vbase]fps={CFG.fps},ass=captions.ass:fontsdir='{fonts}'"
    if kind == "preview":
        tail += f",scale={CFG.preview_w}:{CFG.preview_h}"
    graph += ";" + tail + ",format=yuv420p[vout]"
    tmp = work / ("out" + out.suffix)
    args = ["-ss", f"{start:.3f}", "-i", str(Path(src).resolve())]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-t", f"{dur:.3f}", "-filter_complex", graph, "-map", "[vout]",
             "-map", "0:a:0" if has_audio else "1:a:0",
             "-c:v", "libx264", "-preset", "ultrafast" if kind == "preview" else "veryfast",
             "-crf", "28" if kind == "preview" else "20", "-maxrate", "4M" if kind == "preview" else "12M",
             "-bufsize", "24M", "-r", str(CFG.fps), "-g", str(CFG.fps * 2),
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart", tmp.name]
    # cwd=work lets the ass filter use a bare relative filename (no path-escaping issues)
    run_ffmpeg(args, duration=dur, on_progress=on_progress, cancel=cancel, cwd=work)
    out.parent.mkdir(parents=True, exist_ok=True)
    os.replace(work / tmp.name, out)
    shutil.copy(work / "captions.ass", out.with_suffix(".ass"))
    shutil.rmtree(work, ignore_errors=True)
    return out


# ------------------------------------------------------------ face centring
def face_model_available() -> bool:
    try:
        import cv2
        return Path(cv2.data.haarcascades, "haarcascade_frontalface_default.xml").exists()
    except Exception:
        return False


def auto_speaker_cx(src: Path, start: float, end: float) -> tuple[float | None, str]:
    """Median face centre (0..1) across ~6 frames, or (None, why-not)."""
    if not face_model_available():
        return None, "face model not available in this OpenCV build — centred crop (adjust manually)"
    import cv2
    import numpy as np
    from .media import sample_frames
    det = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")))
    times = [start + (end - start) * k / 7 for k in range(1, 7)]
    xs = []
    for fr in sample_frames(src, times):
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        faces = det.detectMultiScale(g, 1.1, 5, minSize=(24, 24))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + w / 2) / fr.shape[1])
    if not xs:
        return None, "no face found in sampled frames — centred crop"
    return float(np.median(xs)), f"face found in {len(xs)}/6 sampled frames"
