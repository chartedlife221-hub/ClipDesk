"""FFmpeg / ffprobe helpers. Every subprocess is cancellable and reports progress."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts"}
BROWSER_SAFE = {("h264", "aac"), ("h264", None), ("h264", "mp3"), ("vp9", "opus"), ("av1", "opus")}


class MediaError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def probe(path: Path) -> dict:
    """Return duration, video size/fps/codec and audio codec. Raises MediaError if not a video."""
    exe = ffprobe_bin()
    if not exe:
        raise MediaError("ffprobe not found on PATH")
    p = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0:
        raise MediaError(f"ffprobe could not read the file: {p.stderr.strip()[:300] or 'unknown format'}")
    info = json.loads(p.stdout or "{}")
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise MediaError("No video stream found — ClipDesk needs a video file")
    dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise MediaError("Could not determine the video duration")
    fps = 0.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        n, d = rate.split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    except ValueError:
        pass
    return {
        "duration": dur,
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": round(fps, 3),
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "format": info.get("format", {}).get("format_name", ""),
    }


def run_ffmpeg(args: list[str], *, duration: float | None = None,
               on_progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None, timeout: float = 3600,
               cwd: Path | None = None) -> None:
    """Run ffmpeg with -progress parsing. Kills the process if `cancel` is set."""
    exe = ffmpeg_bin()
    if not exe:
        raise MediaError("ffmpeg not found on PATH")
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-loglevel", "error", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(cwd) if cwd else None)
    err_chunks: list[str] = []

    def _drain_err():
        assert proc.stderr
        for line in proc.stderr:
            err_chunks.append(line)

    t = threading.Thread(target=_drain_err, daemon=True)
    t.start()
    assert proc.stdout
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled("cancelled")
        if line.startswith("out_time_us=") and duration and on_progress:
            try:
                us = int(line.split("=", 1)[1])
                on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
            except ValueError:
                pass
    proc.wait(timeout=timeout)
    t.join(timeout=5)
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")
    if proc.returncode != 0:
        raise MediaError("ffmpeg failed: " + "".join(err_chunks).strip()[-600:])


def browser_playable(info: dict, path: Path) -> bool:
    if path.suffix.lower() not in {".mp4", ".m4v", ".webm", ".mov"}:
        return False
    return (info.get("vcodec"), info.get("acodec")) in BROWSER_SAFE


def make_proxy(src: Path, dst: Path, info: dict, **kw) -> None:
    """720p H.264 proxy for in-browser scrubbing when the original isn't browser-safe."""
    args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:'min(720,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-g", "30",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    run_ffmpeg(args, duration=info["duration"], **kw)


def make_poster(src: Path, dst: Path, at: float) -> None:
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", str(dst)])


def extract_wav(src: Path, dst: Path, **kw) -> None:
    """16 kHz mono PCM — the input faster-whisper and the loudness signal both want."""
    info = probe(src)
    if not info["has_audio"]:
        raise MediaError("The source has no audio track")
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
               duration=info["duration"], **kw)


def read_wav_mono16(path: Path) -> tuple[np.ndarray, int]:
    import wave
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def rms_windows(samples: np.ndarray, sr: int, window_s: float) -> np.ndarray:
    """RMS loudness (dBFS) per window. The last partial window is kept if ≥ 1 s long."""
    hop = int(sr * window_s)
    if hop <= 0 or samples.size == 0:
        return np.zeros(0)
    n_full = samples.size // hop
    out = []
    for i in range(n_full):
        seg = samples[i * hop:(i + 1) * hop]
        out.append(np.sqrt(np.mean(seg * seg) + 1e-12))
    tail = samples[n_full * hop:]
    if tail.size >= sr:
        out.append(np.sqrt(np.mean(tail * tail) + 1e-12))
    rms = np.asarray(out, dtype=np.float64)
    return 20 * np.log10(np.maximum(rms, 1e-6))


def sample_frames(src: Path, times: list[float], width: int = 320) -> list[np.ndarray]:
    """Grab small BGR frames at the given times (for face detection)."""
    import cv2  # optional dependency
    frames = []
    exe = ffmpeg_bin()
    for t in times:
        p = subprocess.run([exe, "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=60)
        if p.returncode == 0 and p.stdout:
            img = cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                frames.append(img)
    return frames


def probe_image(path: Path) -> dict:
    """codec/width/height of an image file (raises MediaError if ffprobe can't read it)."""
    exe = ffprobe_bin()
    if not exe:
        raise MediaError("ffprobe not found on PATH")
    p = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise MediaError("not an image")
    st = (json.loads(p.stdout or "{}").get("streams") or [{}])[0]
    if not st.get("codec_name"):
        raise MediaError("not an image")
    return {"codec": st["codec_name"], "width": st.get("width"), "height": st.get("height")}
