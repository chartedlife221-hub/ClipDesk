"""Timestamped transcripts.

Engines, in order of preference:
  1. faster-whisper (local CPU, int8) — word timestamps. First use downloads the model
     (~145 MB for 'base', ~490 MB for 'small') into clipdesk-data/models/.
  2. A transcript file supplied with the source (.srt / .vtt / YouTube json3 / ClipDesk json)
     — e.g. YouTube captions fetched by yt-dlp, or a file the clipper uploads.

Stored format (sources/<id>/transcript.json), times in SOURCE seconds:
  {"engine": str, "language": str, "word_timing": "measured"|"estimated",
   "words": [{"s": float, "e": float, "w": str}], "segments": [{"s","e","text"}]}
"""
from __future__ import annotations

import html
import json
import re
import threading
from pathlib import Path
from typing import Callable

from .config import data_root


class TranscriptUnavailable(RuntimeError):
    pass


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def whisper_model_cached(name: str) -> bool:
    d = data_root() / "models"
    return any(name in p.name for p in d.glob("*")) if d.exists() else False


def transcribe_whisper(wav: Path, duration: float, model_name: str, compute: str,
                       on_progress: Callable[[float, str], None], cancel: threading.Event) -> dict:
    if not whisper_available():
        raise TranscriptUnavailable("faster-whisper is not installed (pip install faster-whisper)")
    from faster_whisper import WhisperModel
    if not whisper_model_cached(model_name):
        on_progress(0.0, f"first run: downloading Whisper '{model_name}' model…")
    model = WhisperModel(model_name, device="cpu", compute_type=compute,
                         download_root=str(data_root() / "models"))
    segs, info = model.transcribe(str(wav), word_timestamps=True, vad_filter=True,
                                  vad_parameters={"min_silence_duration_ms": 700}, beam_size=1)
    words, segments = [], []
    for seg in segs:
        if cancel.is_set():
            raise RuntimeError("cancelled")
        segments.append({"s": round(seg.start, 3), "e": round(seg.end, 3), "text": seg.text.strip()})
        for w in seg.words or []:
            words.append({"s": round(w.start, 3), "e": round(w.end, 3), "w": w.word.strip()})
        on_progress(min(0.99, seg.end / max(duration, 1e-6)), f"transcribing {seg.end:.0f}s / {duration:.0f}s")
    return {"engine": f"faster-whisper:{model_name}", "language": info.language,
            "word_timing": "measured", "words": words, "segments": segments}


# ---------------------------------------------------------------- caption files
TS = r"(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"


def _ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    h, m, sec = parts
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _spread(segments: list[dict]) -> list[dict]:
    """Estimate word timings by spreading each cue's duration over its words (by length)."""
    words = []
    for seg in segments:
        toks = seg["text"].split()
        if not toks:
            continue
        total = sum(len(t) + 1 for t in toks)
        t = seg["s"]
        span = max(seg["e"] - seg["s"], 0.05)
        for tok in toks:
            d = span * (len(tok) + 1) / total
            words.append({"s": round(t, 3), "e": round(t + d, 3), "w": tok})
            t += d
    return words


def parse_caption_file(data: bytes, name: str) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    low = name.lower()
    segments: list[dict] = []
    words: list[dict] = []
    if low.endswith(".json") or low.endswith(".json3") or text.lstrip().startswith("{"):
        obj = json.loads(text)
        if "words" in obj or "segments" in obj:  # ClipDesk / whisper-like
            segments = [{"s": float(s.get("s", s.get("start"))), "e": float(s.get("e", s.get("end"))),
                         "text": s["text"].strip()} for s in obj.get("segments", [])]
            words = [{"s": float(w.get("s", w.get("start"))), "e": float(w.get("e", w.get("end"))),
                      "w": (w.get("w") or w.get("word")).strip()} for w in obj.get("words", [])]
            timing = "measured" if words else "estimated"
            if not words:
                words = _spread(segments)
            return {"engine": "file:" + name, "language": obj.get("language", ""), "word_timing": timing,
                    "words": words, "segments": segments}
        for ev in obj.get("events", []):  # YouTube json3
            if "segs" not in ev:
                continue
            t0 = ev.get("tStartMs", 0) / 1000
            dur = ev.get("dDurationMs", 0) / 1000
            segs = [s for s in ev["segs"] if s.get("utf8", "").strip()]
            for i, s in enumerate(segs):
                st = t0 + s.get("tOffsetMs", 0) / 1000
                nxt = t0 + segs[i + 1].get("tOffsetMs", 0) / 1000 if i + 1 < len(segs) else t0 + dur
                words.append({"s": round(st, 3), "e": round(max(nxt, st + 0.05), 3), "w": s["utf8"].strip()})
            if segs:
                segments.append({"s": t0, "e": t0 + dur, "text": " ".join(s["utf8"].strip() for s in segs)})
        return {"engine": "file:" + name, "language": "", "word_timing": "measured" if words else "estimated",
                "words": words, "segments": segments}
    # SRT / VTT
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    seen = set()
    for b in blocks:
        m = re.search(rf"({TS})\s*-->\s*({TS})", b)
        if not m:
            continue
        s, e = _ts(m.group(1)), _ts(m.group(3))
        body = b[m.end():].split("\n", 1)[1] if "\n" in b[m.end():] else ""
        # YouTube auto-caption inline word timings: <00:00:01.234><c> word</c>
        inline = re.findall(rf"<({TS})><c>([^<]*)</c>", body)
        clean = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        clean = re.sub(r"\s+", " ", clean)
        if not clean:
            continue
        if inline:
            first = re.sub(r"<[^>]+>", "", body.split("<", 1)[0]).strip()
            seq = ([(s, first)] if first else []) + [(_ts(t), w.strip()) for t, _, w in inline]
            for i, (ts, w) in enumerate(seq):
                te = seq[i + 1][0] if i + 1 < len(seq) else e
                if w and (round(ts, 2), w) not in seen:
                    seen.add((round(ts, 2), w))
                    words.append({"s": round(ts, 3), "e": round(max(te, ts + 0.05), 3), "w": w})
        key = (round(s, 2), clean)
        if key in seen:
            continue
        seen.add(key)
        # rolling YouTube captions repeat the previous line; keep only the new tail
        if segments and clean.startswith(segments[-1]["text"]) and s <= segments[-1]["e"] + 0.05:
            tail = clean[len(segments[-1]["text"]):].strip()
            if not tail:
                continue
            clean = tail
        segments.append({"s": s, "e": e, "text": clean})
    timing = "measured" if words else "estimated"
    if not words:
        words = _spread(segments)
    return {"engine": "file:" + name, "language": "", "word_timing": timing,
            "words": sorted(words, key=lambda w: w["s"]), "segments": segments}


# ---------------------------------------------------------------- structure
def sentences(tr: dict, max_gap: float = 1.2, max_words: int = 40) -> list[dict]:
    """Group words into sentences: [{'s','e','text','i0','i1'}] (word index range, inclusive)."""
    words = tr.get("words") or []
    out: list[dict] = []
    start = 0
    for i, w in enumerate(words):
        end_here = bool(re.search(r"[.?!…]['\"”)]?$", w["w"]))
        gap = (words[i + 1]["s"] - w["e"]) if i + 1 < len(words) else 99
        if end_here or gap > max_gap or (i - start + 1) >= max_words or i == len(words) - 1:
            seg = words[start:i + 1]
            out.append({"s": seg[0]["s"], "e": seg[-1]["e"], "i0": start, "i1": i,
                        "text": " ".join(x["w"] for x in seg).strip()})
            start = i + 1
    return out


def words_between(tr: dict, s: float, e: float) -> list[dict]:
    return [w for w in tr.get("words", []) if w["e"] > s and w["s"] < e]


def caption_lines(tr: dict, s: float, e: float, max_chars: int = 32, max_dur: float = 2.8) -> list[dict]:
    """Short burn-in caption chunks (SOURCE time) for the range [s, e]."""
    ws = words_between(tr, s, e)
    lines, cur = [], []
    for w in ws:
        txt = " ".join(x["w"] for x in cur + [w])
        if cur and (len(txt) > max_chars or w["e"] - cur[0]["s"] > max_dur or w["s"] - cur[-1]["e"] > 0.8):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [{"s": round(max(l[0]["s"], s), 3), "e": round(min(l[-1]["e"], e), 3),
             "text": " ".join(x["w"] for x in l)} for l in lines if l]


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
