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


# ============================================================ v0.3 caption styles
# Each preset renders the SAME caption lines (revision captions, source time) differently.
#   mode "line"    : one event per chunk
#   mode "pop"     : the word being spoken is coloured and scaled up
#   mode "karaoke" : spoken words white, the current word coloured, upcoming words dimmed
PRESETS: dict[str, dict] = {
    "clean":    {"label": "Clean", "font": "Fira Sans", "bold": True, "upper": False, "outline": 5, "shadow": 2,
                 "box": False, "mode": "line", "words": 0, "wf": 0.52, "size": 1.0},
    "pop":      {"label": "Pop", "font": "Fira Sans ExtraBold", "bold": False, "upper": True, "outline": 7, "shadow": 3,
                 "box": False, "mode": "pop", "words": 3, "wf": 0.62, "size": 1.05},
    "karaoke":  {"label": "Karaoke", "font": "Fira Sans", "bold": True, "upper": False, "outline": 5, "shadow": 2,
                 "box": False, "mode": "karaoke", "words": 0, "wf": 0.52, "size": 1.0},
    "boxed":    {"label": "Boxed", "font": "Fira Sans", "bold": True, "upper": False, "outline": 16, "shadow": 0,
                 "box": True, "mode": "line", "words": 0, "wf": 0.52, "size": 0.92},
    "headline": {"label": "Headline", "font": "Anton", "bold": False, "upper": True, "outline": 6, "shadow": 3,
                 "box": False, "mode": "pop", "words": 2, "wf": 0.47, "size": 1.3},
}
HIGHLIGHTS = ["#FFE14D", "#3CF2A3", "#4FD8FF", "#FF6FB5", "#FFFFFF"]
DEFAULT_STYLE = {"preset": "clean", "color": "#FFE14D", "words": None, "upper": None}


def resolve_style(layout: dict, default_size: int) -> dict:
    """Merge a clip's caption style over its preset. `words`/`upper` None = preset default."""
    st = {**DEFAULT_STYLE, **((layout or {}).get("style") or {})}
    p = PRESETS.get(st["preset"], PRESETS["clean"])
    words = p["words"] if st.get("words") in (None, "") else int(st["words"])
    upper = p["upper"] if st.get("upper") is None else bool(st["upper"])
    color = st.get("color") if isinstance(st.get("color"), str) and len(st["color"]) == 7 else "#FFE14D"
    base = int((layout or {}).get("caption_size") or default_size)
    return {**p, "preset": st["preset"] if st["preset"] in PRESETS else "clean", "color": color,
            "words": max(0, min(8, words)), "upper": upper, "px": int(round(base * p["size"]))}


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper().replace("&H", "&H", 1)


def _norm_tok(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def line_words(line: dict, tr_words: list[dict] | None) -> list[dict]:
    """Word timings inside one caption line.

    Uses the transcript's measured words when they still match the (possibly edited) line text;
    otherwise spreads the line's duration across its words by length.
    """
    toks = line["text"].split()
    if not toks:
        return []
    s, e = float(line["s"]), float(line["e"])
    cand = [w for w in (tr_words or []) if w["e"] > s + 0.01 and w["s"] < e - 0.01]
    if cand and [_norm_tok(w["w"]) for w in cand] == [_norm_tok(t) for t in toks]:
        out = []
        for i, (w, t) in enumerate(zip(cand, toks)):
            ws = s if i == 0 else max(s, float(w["s"]))
            we = e if i == len(toks) - 1 else min(e, float(w["e"]))
            out.append({"s": round(ws, 3), "e": round(max(we, ws + 0.04), 3), "w": t})
        return out
    total = sum(len(t) + 1 for t in toks)
    out, t0 = [], s
    for t in toks:
        d = (e - s) * (len(t) + 1) / total
        out.append({"s": round(t0, 3), "e": round(t0 + d, 3), "w": t})
        t0 += d
    out[-1]["e"] = round(e, 3)
    return out


def chunk_words(line: dict, words: list[dict], n: int) -> list[dict]:
    """Split a line's words into groups of n (0 = whole line). Chunk times tile the line exactly."""
    if not words:
        return []
    groups = [words] if n <= 0 else [words[i:i + n] for i in range(0, len(words), n)]
    out = []
    for gi, g in enumerate(groups):
        cs = float(line["s"]) if gi == 0 else g[0]["s"]
        ce = float(line["e"]) if gi == len(groups) - 1 else groups[gi + 1][0]["s"]
        out.append({"s": round(cs, 3), "e": round(ce, 3), "words": g})
    return out


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap_words(words: list[str], n: int) -> list[list[int]]:
    """Same balanced wrapping as wrap_lines, but returns word-index groups per output line."""
    text = " ".join(words)
    lines = wrap_lines(text, n)
    out, i = [], 0
    for ln in lines:
        k = len(ln.split())
        out.append(list(range(i, i + k)))
        i += k
    return out


def build_styled_ass(local: list[dict], tr_words: list[dict] | None, style: dict, *, w: int = 1080, h: int = 1920,
                     margin_v: int = 384) -> str:
    """ASS for any preset. `local` lines and `tr_words` are clip-local seconds."""
    margin_lr = 90
    size = style["px"]
    bold = -1 if style["bold"] else 0
    hi = _ass_color(style["color"])
    if style["box"]:
        border, back, outline_col = 3, "&H50000000", "&H38101318"
    else:
        border, back, outline_col = 1, "&H64000000", "&H00000000"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style['font']},{size},&H00FFFFFF,&H00FFFFFF,{outline_col},{back},{bold},0,0,0,100,100,0,0,{border},{style['outline']},{style['shadow']},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    n_chars = max(8, int((w - 2 * margin_lr) / (size * style["wf"])))
    events = []
    for line in local:
        words = line_words(line, tr_words)
        for ch in chunk_words(line, words, style["words"]):
            toks = [(_esc(x["w"]).upper() if style["upper"] else _esc(x["w"])) for x in ch["words"]]
            rows = wrap_words(toks, n_chars)

            def render(active: int | None) -> str:
                parts = []
                for r in rows:
                    seg = []
                    for i in r:
                        t = toks[i]
                        if active is None or style["mode"] == "line":
                            seg.append(t)
                        elif style["mode"] == "pop":
                            seg.append(f"{{\\c{hi}\\fscx112\\fscy112}}{t}{{\\r}}" if i == active else t)
                        else:  # karaoke
                            if i == active:
                                seg.append(f"{{\\c{hi}}}{t}{{\\r}}")
                            elif i > active:
                                seg.append(f"{{\\1a&H70&}}{t}{{\\r}}")
                            else:
                                seg.append(t)
                    parts.append(" ".join(seg))
                return r"\N".join(parts)

            if style["mode"] == "line":
                events.append((ch["s"], ch["e"], render(None)))
            else:
                ws = ch["words"]
                for i, wd in enumerate(ws):
                    a = ch["s"] if i == 0 else wd["s"]
                    b = ch["e"] if i == len(ws) - 1 else ws[i + 1]["s"]
                    if b - a >= 0.02:
                        events.append((a, b, render(i)))
    body = "\n".join(f"Dialogue: 0,{_ass_t(a)},{_ass_t(b)},Cap,,0,0,0,,{t}" for a, b, t in events)
    return head + body + "\n"
