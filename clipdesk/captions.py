"""Captions: source-time lines → clip-local ASS (burn-in) and SRT (sidecar).

Revisions store captions in SOURCE seconds. On export each line is shifted by -clip_start,
clipped to [0, clip_duration] and dropped if nothing remains — so trimming never desyncs.
"""
from __future__ import annotations

import textwrap


def to_local(captions: list[dict], start: float, end: float) -> list[dict]:
    out = []
    dur = end - start
    for c in captions:
        s = max(0.0, float(c["s"]) - start)
        e = min(dur, float(c["e"]) - start)
        txt = (c.get("text") or "").strip()
        if e - s >= 0.05 and txt:
            out.append({"s": round(s, 3), "e": round(e, 3), "text": txt})
    return out


def _ass_t(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_t(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def max_chars(size: int, width_px: int) -> int:
    # ~0.52 em average glyph width for Fira Sans Bold (the bundled caption font)
    return max(10, int(width_px / (size * 0.52)))


def wrap_lines(text: str, n: int) -> list[str]:
    """Wrap to lines of ≤ n chars; when it takes two lines, balance them (no one-word orphans)."""
    lines = textwrap.wrap(text, n) or [""]
    if len(lines) == 2:
        words = text.split()
        best = min(range(1, len(words)), key=lambda k: max(len(" ".join(words[:k])), len(" ".join(words[k:]))))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    return lines


def wrap(text: str, size: int, width_px: int) -> str:
    return r"\N".join(wrap_lines(text, max_chars(size, width_px)))


def build_ass(local: list[dict], *, w: int = 1080, h: int = 1920, font: str = "Fira Sans",
              size: int = 68, margin_v: int | None = None, position: str = "lower") -> str:
    margin_lr = 90
    if margin_v is None:
        margin_v = int(h * (0.22 if position == "lower" else 0.45))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in local:
        txt = c["text"].replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 0,{_ass_t(c['s'])},{_ass_t(c['e'])},Cap,,0,0,0,,{wrap(txt, size, w - 2 * margin_lr)}")
    return head + "\n".join(lines) + "\n"


def build_srt(local: list[dict]) -> str:
    out = []
    for i, c in enumerate(local, 1):
        out.append(f"{i}\n{_srt_t(c['s'])} --> {_srt_t(c['e'])}\n{c['text']}\n")
    return "\n".join(out)
