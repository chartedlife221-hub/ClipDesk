"""Candidate discovery.

Gaming  — appendix §6 Stage 1: 5-second windows, each signal z-scored against the stream's own
          rolling baseline, squashed, and combined C = .4 audio + .4 chat + .2 hype (+0 laugh).
          Boundaries look BACK from the reaction for the play/setup and FORWARD for the payoff.
Speech  — search the whole timestamped transcript for the clipper's request (loudness plays
          no part, so a quiet answer can rank first). Boundaries keep complete sentences, extend
          forward to the requested payoff ("keep the lesson") and back to an interview question.

Everything here is deterministic (same inputs → same candidates).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .config import CFG, Config
from .transcribe import sentences


def fmt_tc(t: float, precise: bool = False) -> str:
    t = max(0.0, t)
    if precise:
        h, rem = divmod(t, 3600)
        m, sec = divmod(rem, 60)
        return f"{int(h):d}:{int(m):02d}:{sec:04.1f}" if h >= 1 else f"{int(m):d}:{sec:04.1f}"
    h, rem = divmod(int(round(t)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


@dataclass
class Candidate:
    start: float
    end: float
    anchor: float
    score: float
    strength: str            # strong | weak
    title: str
    reason: str
    hashtags: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    captions_hint: str = ""


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    note: str
    mode: str


# ============================================================ text utilities
STOP = set("""a an the and or but if then so of to in on at for with from by about into over after before
is are was were be been being am do does did doing have has had i you he she it we they me him her us them
my your his its our their this that these those there here what which who whom when where why how
can could should would will shall may might must just very really also too not no yes up out
find show get give clip moment part bit where want please one some any all
story thing segment section bit time when talk talks talking says said say mentions mention
discuss discusses discussing explains explain describes describe speaks speak goes he she they guy""".split())

# Small hand-written concept lexicon used by the LOCAL ranker. Keys are stems.
CONCEPTS: dict[str, list[str]] = {
    "fail": ["fail", "failed", "failure", "failing", "bankrupt", "bankruptcy", "went under", "go under",
             "collapsed", "flopped", "flop", "lost everything", "shut down", "shut it down", "closed down",
             "went bust", "broke", "didn't work", "did not work", "mistake"],
    "business": ["business", "company", "startup", "start-up", "venture", "shop", "store", "restaurant",
                 "brand", "firm", "agency", "side hustle", "café", "cafe"],
    "first": ["first", "initial", "original", "my early", "very first", "started"],
    "lesson": ["lesson", "learned", "learnt", "taught me", "teach", "realized", "realised", "takeaway",
               "the moral", "figured out", "what i learned", "the lesson", "advice", "now i know"],
    "surpris": ["surprise", "surprising", "unexpected", "shocked", "no way", "couldn't believe",
                "can't believe", "insane", "crazy", "unbelievable", "what the", "how did", "wtf", "omg"],
    "reaction": ["react", "reaction", "laugh", "scream", "yell", "oh my god", "no way", "let's go",
                 "lets go"],
    "play": ["play", "shot", "kill", "clutch", "ace", "snipe", "headshot", "move", "outplay", "win",
             "won", "round", "boss"],
    "money": ["money", "dollars", "cash", "paid", "salary", "debt", "savings", "invest", "revenue"],
    "quit": ["quit", "quitting", "resign", "left my job", "walked away", "gave up"],
    "fear": ["fear", "afraid", "scared", "terrified", "nervous", "anxious"],
}
HYPE_TOKENS = {"pog", "pogchamp", "poggers", "kekw", "lul", "lulw", "omegalul", "monkas", "pepelaugh",
               "w", "ww", "www", "lmao", "lmfao", "lol", "omg", "wtf", "clip", "clipit", "insane", "holy",
               "letsgo", "lets", "gg", "ez", "no way", "noway", "😂", "🔥", "💀", ":o", "sheesh", "goated"}


def norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9'\s-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def stem(w: str) -> str:
    for suf in ("ingly", "ing", "edly", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def phrase_in(phrase: str, text_norm: str, toks_stem: set[str]) -> bool:
    p = norm(phrase)
    if " " in p or "'" in p:
        return f" {p} " in f" {text_norm} "
    return stem(p) in toks_stem


@dataclass
class Concept:
    label: str
    phrases: list[str]
    literal: str = ""   # the clipper's own word (full weight); other phrases are synonyms (0.7)


SYNONYM_WEIGHT = 0.7


def concept_hit(c: Concept, text_norm: str, toks_stem: set[str]) -> float:
    if c.literal and phrase_in(c.literal, text_norm, toks_stem):
        return 1.0
    return SYNONYM_WEIGHT if any(phrase_in(p, text_norm, toks_stem) for p in c.phrases) else 0.0


def parse_request(text: str) -> tuple[list[Concept], list[Concept], list[str]]:
    """Split 'Find X and keep Y' into find-concepts and keep-concepts. Returns quoted phrases too."""
    raw = text.strip()
    quoted = re.findall(r"[\"“']([^\"”']{3,})[\"”']", raw)
    low = raw.lower()
    m = re.search(r"\b(?:and\s+)?(?:keep|include|with|until|through)\s+(?:the\s+)?(.+)$", low)
    find_part, keep_part = (low[: m.start()], m.group(1)) if m else (low, "")

    def concepts(part: str) -> list[Concept]:
        out: list[Concept] = []
        seen: set[str] = set()
        for tok in norm(part).split():
            if tok in STOP or len(tok) < 3:
                continue
            st = stem(tok)
            key = next((k for k, v in CONCEPTS.items() if st.startswith(k) or k.startswith(st)
                        or any(stem(norm(p)) == st for p in v if " " not in p)), None)
            label = key or st
            if label in seen:
                continue
            seen.add(label)
            phrases = list(CONCEPTS[key]) if key else []
            phrases.append(tok)
            out.append(Concept(label=tok, phrases=phrases, literal=tok))
        return out

    return concepts(find_part), concepts(keep_part), quoted


# ============================================================ speech search
def speech_search(transcript: dict, request: str, duration: float, cfg: Config = CFG,
                  creator: str = "") -> DiscoveryResult:
    sents = sentences(transcript)
    if not sents:
        return DiscoveryResult([], "The transcript is empty — nothing to search.", "speech")
    find, keep, quoted = parse_request(request)
    if not find and not quoted:
        return DiscoveryResult([], "Describe what to look for (e.g. 'the story about the first failed business').",
                               "speech")
    sn = [norm(s["text"]) for s in sents]
    st = [set(stem(t) for t in x.split()) for x in sn]
    N = len(sents)

    def hits(concepts: list[Concept]) -> list[list[float]]:
        return [[concept_hit(c, sn[i], st[i]) for c in concepts] for i in range(N)]

    fh = hits(find)
    kh = hits(keep)
    qh = [[norm(q) in sn[i] for q in quoted] for i in range(N)]
    # inverse document frequency per concept (rarer = more informative)
    idf = []
    for j in range(len(find)):
        df = sum(1 for i in range(N) if fh[i][j])
        idf.append(math.log(1 + N / (1 + df)) if df else 0.0)
    # denominator: present concepts weigh their idf; concepts absent from the whole transcript
    # weigh the maximum, so a request the media can't satisfy never scores as a full match
    idf_total = sum(v if v > 0 else math.log(1 + N) for v in idf) or 1.0

    if not any(any(r) for r in fh) and not any(any(r) for r in qh):
        terms = ", ".join(c.label for c in find) or ", ".join(quoted)
        return DiscoveryResult([], f"No strong match: no passage in the {fmt_tc(duration)} transcript mentions {terms}.",
                               "speech")

    FIND_SPAN = min(30.0, cfg.max_clip_s * 0.7)
    raw: list[tuple[float, int, int, dict]] = []
    for i in range(N):
        if not (any(fh[i]) or any(qh[i])):
            continue
        best = None
        j = i
        matched: dict[int, float] = {}
        qmatched = False
        while j < N and sents[j]["e"] - sents[i]["s"] <= FIND_SPAN:
            for k, v in enumerate(fh[j]):
                if v > matched.get(k, 0):
                    matched[k] = v
            qmatched = qmatched or any(qh[j])
            cov = sum(idf[k] * w for k, w in matched.items()) / idf_total
            cov = min(1.0, cov) if find else 0.0
            if quoted:
                cov = 0.5 * cov + 0.5 * (1.0 if qmatched else 0.0) if find else (1.0 if qmatched else 0.0)
            # prefer compact windows: tiny penalty per extra sentence
            val = cov - 0.01 * (j - i)
            if best is None or val > best[0] + 1e-9:
                best = (val, i, j, dict(matched))
            j += 1
        if best:
            raw.append(best)

    raw.sort(key=lambda r: (-r[0], sents[r[1]]["s"]))
    picked: list[tuple[float, int, int, dict]] = []
    for r in raw:
        s0, e0 = sents[r[1]]["s"], sents[r[2]]["e"]
        if any(not (e0 <= sents[p[1]]["s"] or s0 >= sents[p[2]]["e"]) for p in picked):
            continue  # overlapping suggestion — merged into the higher-ranked one
        picked.append(r)
        if len(picked) >= cfg.max_candidates:
            break

    cands: list[Candidate] = []
    for cov, i, j, matched in picked:
        cov = max(cov, 0.0)
        notes = []
        # complete thought: a window that starts mid-thought pulls in the previous sentence
        if i > 0 and re.match(r"^(and|so|but|because|which|that's why|then|or)\b", sn[i]) \
                and sents[j]["e"] - sents[i - 1]["s"] <= cfg.max_clip_s:
            i -= 1
        # interview question supplying context
        q_idx = None
        for back in (i - 1, i - 2):
            if back >= 0 and sents[back]["text"].rstrip().endswith("?") and sents[i]["s"] - sents[back]["e"] < 8 \
                    and sents[j]["e"] - sents[back]["s"] <= cfg.max_clip_s:
                q_idx = back
                break
        if q_idx is not None:
            notes.append(f"includes the question at {fmt_tc(sents[q_idx]['s'])} (“{_excerpt(sents[q_idx]['text'], 70)}”)")
            i = q_idx
        # payoff: extend forward to the requested 'keep' part
        keep_idx = None
        if keep:
            for k in range(j, N):
                if sents[k]["e"] - sents[i]["s"] > cfg.max_clip_s:
                    break
                if any(kh[k]):
                    keep_idx = k
                    break
            if keep_idx is not None:
                j = max(j, keep_idx)
        if sents[j]["text"].rstrip().endswith("?"):
            # a window ending on a question needs its answer
            k = j + 1
            while k < N and sents[k]["e"] - sents[i]["s"] <= cfg.max_clip_s and \
                    sents[k]["s"] - sents[j]["e"] < 4 and (k == j + 1 or sents[k]["e"] - sents[j + 1]["s"] < 20):
                if sents[k]["text"].rstrip().endswith("?"):
                    break
                k += 1
            j = max(j, k - 1)
        if not keep:
            # no explicit payoff requested: finish the thought (one more sentence if it continues)
            if j + 1 < N and re.match(r"^(and|so|but|because|which)\b", sn[j + 1]) \
                    and sents[j + 1]["e"] - sents[i]["s"] <= cfg.max_clip_s:
                j += 1
        start, end = sents[i]["s"], sents[j]["e"]
        start = max(0.0, start - 0.25)
        end = min(duration, end + 0.35)
        score = 0.75 * cov + (0.25 if keep_idx is not None else 0.0) if keep else cov
        strong = cov >= 0.5 and (not keep or keep_idx is not None) and score >= cfg.strong_match
        anchor_i = max(range(i, j + 1), key=lambda x: (sum(1 for v in fh[x] if v) + sum(qh[x]),
                                                       not sents[x]["text"].rstrip().endswith("?"), -x))
        matched_labels = [find[k].label for k in sorted(matched)]
        reason = []
        if matched_labels:
            where = []
            for k in sorted(matched):
                x = next(x for x in range(i, j + 1) if fh[x][k])
                c = find[k]
                said = c.literal if fh[x][k] == 1.0 else next(p for p in c.phrases if phrase_in(p, sn[x], st[x]))
                where.append(f"'{c.label}' at {fmt_tc(sents[x]['s'])}" + (f" (as “{said}”)" if said != c.literal else ""))
            reason.append("Mentions " + ", ".join(where) + f" — “{_excerpt(sents[anchor_i]['text'], 100)}”")
        if quoted and any(any(qh[x]) for x in range(i, j + 1)):
            reason.append("contains the quoted phrase")
        if keep:
            if keep_idx is not None:
                reason.append(f"payoff ({', '.join(c.label for c in keep)}) at {fmt_tc(sents[keep_idx]['s'])}: "
                              f"“{_excerpt(sents[keep_idx]['text'], 90)}”")
            else:
                reason.append(f"no '{' '.join(c.label for c in keep)}' found within {cfg.max_clip_s:.0f}s after it")
        missing = [find[k].label for k in range(len(find)) if k not in matched]
        if missing:
            reason.append(f"does not mention {', '.join(repr(m) for m in missing)}")
        reason += notes
        title = _title_from(sents[anchor_i]["text"])
        tags = _hashtags(creator, matched_labels + [c.label for c in keep if keep_idx is not None],
                         " ".join(sn[i:j + 1]))
        cands.append(Candidate(start=round(start, 2), end=round(end, 2), anchor=round(sents[anchor_i]["s"], 2),
                               score=round(score, 3), strength="strong" if strong else "weak", title=title,
                               reason="; ".join(reason) + ".", hashtags=tags,
                               signals={"coverage": round(cov, 3), "payoff": keep_idx is not None,
                                        "question": q_idx is not None, "engine": "local-lexical"}))
    cands = _nms(cands, contain=True)  # boundary extension can re-introduce overlap: merge again
    cands.sort(key=lambda c: (-c.score, c.start))
    if not any(c.strength == "strong" for c in cands):
        note = (f"No strong match. Closest {len(cands)} passage(s) shown as weak — "
                f"they mention only part of the request.")
    else:
        note = f"{sum(c.strength == 'strong' for c in cands)} strong match(es) in {fmt_tc(duration)} of transcript."
    return DiscoveryResult(cands, note, "speech")


def _excerpt(t: str, n: int) -> str:
    t = t.strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _title_from(sentence: str) -> str:
    t = re.sub(r"^(so|and|but|well|um|uh|like|you know)[, ]+", "", sentence.strip(), flags=re.I)
    t = t.rstrip(".?! ")
    t = _excerpt(t, 64)
    return t[:1].upper() + t[1:] if t else "Untitled clip"


def _hashtags(creator: str, labels: list[str], text_norm: str) -> list[str]:
    tags = []
    if creator:
        tags.append("#" + re.sub(r"[^A-Za-z0-9]", "", creator))
    for l in labels:
        w = re.sub(r"[^a-z0-9]", "", l.lower())
        if w and w in text_norm.replace(" ", "") and ("#" + w) not in tags:
            tags.append("#" + w)
    for g in ("#shorts", "#clips"):
        tags.append(g)
    return tags[:6]


# ============================================================ gaming detector
def squash(z: np.ndarray, c: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(z - c)))


def rolling_z(x: np.ndarray, base_n: int, min_n: int) -> tuple[np.ndarray, str]:
    """z of each window against the previous `base_n` windows; global stats when history is short.

    Returns (z, note). Zero variance yields z = 0 (no spike can be claimed from a flat signal).
    """
    n = x.size
    if n == 0:
        return x.astype(float), "empty"
    gm, gs = float(np.mean(x)), float(np.std(x))
    z = np.zeros(n)
    flat = gs < 1e-9
    for i in range(n):
        hist = x[max(0, i - base_n):i]
        if hist.size >= min_n:
            m, s = float(np.mean(hist)), float(np.std(hist))
            if s < 1e-9:
                s = gs
        else:
            m, s = gm, gs
        z[i] = 0.0 if s < 1e-9 else (x[i] - m) / s
    note = "flat (zero variance)" if flat else ("whole-recording baseline" if n < min_n * 2 else "rolling baseline")
    return z, note


def chat_buckets(chat: list[dict], n: int, window_s: float) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    counts = np.zeros(n)
    hype = np.zeros(n)
    tok_by_win: list[dict] = [dict() for _ in range(n)]
    for m in chat:
        i = int(m["t"] // window_s)
        if 0 <= i < n:
            counts[i] += 1
            text = m.get("text", "")
            low = text.lower()
            toks = re.findall(r"[a-z0-9']+|[😂🔥💀]", low)
            h = 0
            for t in toks:
                if t in HYPE_TOKENS:
                    h += 1
                    tok_by_win[i][t] = tok_by_win[i].get(t, 0) + 1
            if "no way" in low:
                h += 1
                tok_by_win[i]["no way"] = tok_by_win[i].get("no way", 0) + 1
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
                h += 1
            if "!!!" in text:
                h += 1
            hype[i] += h
    return counts, hype, tok_by_win


def gaming_detect(rms_db: np.ndarray, chat: list[dict] | None, duration: float, request: str = "",
                  transcript: dict | None = None, cfg: Config = CFG, creator: str = "") -> DiscoveryResult:
    W = cfg.window_s
    n = rms_db.size
    if n < 3:
        return DiscoveryResult([], f"Recording is only {duration:.1f}s — too short for the "
                                   f"{W:.0f}-second signal windows. Clip it manually instead.", "gaming")
    base_n = int(cfg.baseline_s / W)
    za, na = rolling_z(rms_db, base_n, cfg.min_baseline_windows)
    notes = [f"audio: {na}"]
    have_chat = bool(chat)
    if have_chat:
        cc, hy, toks = chat_buckets(chat, n, W)
        zc, nc = rolling_z(cc, base_n, cfg.min_baseline_windows)
        zh, nh = rolling_z(hy, base_n, cfg.min_baseline_windows)
        notes.append(f"chat: {nc}")
        weights = (cfg.w_audio, cfg.w_chat, cfg.w_hype)
    else:
        cc = hy = np.zeros(n)
        toks = [dict() for _ in range(n)]
        zc = zh = np.zeros(n)
        notes.append("chat: not available — scored on loudness only")
        weights = (1.0, 0.0, 0.0)
    sa, sc, sh = squash(za, cfg.squash_center), squash(zc, cfg.squash_center), squash(zh, cfg.squash_center)
    C = weights[0] * sa + weights[1] * sc + weights[2] * sh
    if np.std(rms_db) < 1e-9 and not have_chat:
        return DiscoveryResult([], "No strong match: the audio level is completely flat and there is no chat log, "
                                   "so there is no reaction signal to rank.", "gaming")

    # group above-gate windows into events, bridging gaps of up to 2 windows (a play followed a few
    # seconds later by the reaction is ONE moment); anchor = strongest window (ties → loudest)
    above = C >= cfg.gate
    events = []
    i = 0
    while i < n:
        if above[i]:
            j = i
            while True:
                nxt = next((k for k in range(j + 1, min(n, j + 4)) if above[k]), None)
                if nxt is None:
                    break
                j = nxt
            pk = i + int(np.argmax(C[i:j + 1] + 1e-3 * za[i:j + 1]))
            events.append((i, j, pk))
            i = j + 1
        else:
            i += 1

    # request relevance (transcript words + chat text inside the window)
    find, keep, _ = parse_request(request) if request.strip() else ([], [], [])
    tr_sents = sentences(transcript) if transcript else []

    cands: list[Candidate] = []
    for (i0, i1, pk) in events:
        anchor = pk * W + W / 2
        # look back for the setup: last calm window (audio z < 0.5) before the rise
        s_idx = i0
        lim = max(0, i0 - int(cfg.pre_roll_max_s / W))
        while s_idx - 1 >= lim and za[s_idx - 1] >= 0.5:
            s_idx -= 1
        start = max(0.0, s_idx * W - min(5.0, cfg.pre_roll_max_s))  # include the play leading in
        start = max(start, anchor - cfg.pre_roll_max_s)
        # look forward for the payoff: until the reaction decays
        e_idx = i1
        lim_e = min(n - 1, i1 + int(cfg.post_roll_max_s / W))
        while e_idx + 1 <= lim_e and (C[e_idx + 1] >= cfg.gate * 0.6 or za[e_idx + 1] >= 1.0):
            e_idx += 1
        end = min(duration, (e_idx + 1) * W + 2.0)
        if end - start > cfg.max_clip_s:          # keep the reaction, trim setup first
            start = max(start, end - cfg.max_clip_s)
            if anchor - start < 6:
                start = max(0.0, anchor - 6)
                end = start + cfg.max_clip_s
        # snap to sentence edges if a transcript exists (avoid cutting words)
        start, end = _snap(tr_sents, start, end, duration, cfg.max_clip_s)
        peak = float(C[pk])
        rel, rel_hits = _relevance(find + keep, tr_sents, chat or [], start, end)
        score = 0.8 * peak + 0.2 * rel if (find or keep) else peak
        win_toks = {}
        for w in range(i0, i1 + 1):
            for k, v in toks[w].items():
                win_toks[k] = win_toks.get(k, 0) + v
        typical_chat = float(np.median(cc)) if have_chat else 0.0
        parts = [f"Loudness {za[pk]:+.1f}σ vs this recording's baseline at {fmt_tc(pk * W)}–{fmt_tc((pk + 1) * W)}"]
        if have_chat:
            parts.append(f"chat {int(cc[pk])} msgs/{W:.0f}s (typical {typical_chat:.0f})")
            if win_toks:
                top = sorted(win_toks.items(), key=lambda kv: -kv[1])[:3]
                parts.append("hype " + ", ".join(f"{k}×{v}" for k, v in top))
        else:
            parts.append("no chat log, loudness only")
        parts.append(f"setup kept from {fmt_tc(start)} (reaction at {fmt_tc(anchor)})")
        if rel_hits:
            parts.append("request terms heard/seen: " + ", ".join(rel_hits[:4]))
        title = _gaming_title(tr_sents, chat or [], start, end, anchor, win_toks)
        strength = "strong" if peak >= 0.6 else "weak"
        cands.append(Candidate(start=round(start, 2), end=round(end, 2), anchor=round(anchor, 2),
                               score=round(score, 3), strength=strength, title=title,
                               reason="; ".join(parts) + ".",
                               hashtags=_hashtags(creator, [h.split("'")[1] if "'" in h else h for h in rel_hits],
                                                  norm(" ".join(m["text"] for m in (chat or [])
                                                                if start <= m["t"] <= end))) ,
                               signals={"audio_z": round(float(za[pk]), 2), "chat_z": round(float(zc[pk]), 2),
                                        "hype_z": round(float(zh[pk]), 2), "C": round(peak, 3),
                                        "audio": round(float(sa[pk]), 3), "chat": round(float(sc[pk]), 3),
                                        "hype": round(float(sh[pk]), 3), "relevance": round(rel, 3),
                                        "engine": "signals-v1", "chat_available": have_chat}))
    cands = _nms(cands)
    cands.sort(key=lambda c: (-c.score, c.start))
    cands = cands[: cfg.max_candidates]
    if not cands:
        return DiscoveryResult([], f"No strong match: no 5-second window rose above the gate "
                                   f"(peak combined score {float(C.max()):.2f} < {cfg.gate}). "
                                   f"Signals — {'; '.join(notes)}.", "gaming")
    strong = sum(c.strength == "strong" for c in cands)
    note = (f"{strong} strong / {len(cands) - strong} weak reaction moments. " if strong else
            "No strong match — the moments below only rise slightly above this recording's baseline. ") \
        + "Signals — " + "; ".join(notes) + "."
    return DiscoveryResult(cands, note, "gaming")


def _relevance(concepts: list[Concept], sents: list[dict], chat: list[dict], s: float, e: float):
    if not concepts:
        return 0.0, []
    text = " ".join(x["text"] for x in sents if x["e"] > s and x["s"] < e)
    text += " " + " ".join(m["text"] for m in chat if s <= m["t"] <= e)
    tn = norm(text)
    ts = set(stem(t) for t in tn.split())
    got = []
    total = 0.0
    for c in concepts:
        w = concept_hit(c, tn, ts)
        if w:
            hit = c.literal if w == 1.0 else next(p for p in c.phrases if phrase_in(p, tn, ts))
            got.append(f"{c.label} ('{hit}')")
            total += w
    return total / len(concepts), got


def _snap(sents: list[dict], start: float, end: float, duration: float, max_len: float) -> tuple[float, float]:
    if not sents:
        return start, end
    for s in sents:
        if s["s"] <= start < s["e"] and start - s["s"] < 3.0:
            start = s["s"]
            break
    for s in sents:
        if s["s"] < end <= s["e"] and s["e"] - end < 3.0 and s["e"] - start <= max_len:
            end = s["e"]
            break
    return max(0.0, start), min(duration, end)


def _gaming_title(sents, chat, s, e, anchor, win_toks) -> str:
    near = [x for x in sents if x["e"] > anchor - 3 and x["s"] < anchor + 6]
    if near:
        best = max(near, key=lambda x: len(x["text"]))
        return _title_from(best["text"])
    msgs = [m["text"] for m in chat if anchor - 5 <= m["t"] <= anchor + 10 and len(m["text"]) > 3]
    if msgs:
        from collections import Counter
        top = Counter(norm(m) for m in msgs).most_common(1)[0][0]
        return f"Chat: “{_excerpt(top, 40)}” at {fmt_tc(anchor)}"
    return f"Reaction spike at {fmt_tc(anchor)}"


def _nms(cands: list[Candidate], contain: bool = False) -> list[Candidate]:
    """Merge overlapping suggestions, keeping the higher-scoring one."""
    cands = sorted(cands, key=lambda c: (-c.score, c.start))
    kept: list[Candidate] = []
    for c in cands:
        dup = False
        for k in kept:
            inter = max(0.0, min(c.end, k.end) - max(c.start, k.start))
            union = max(c.end, k.end) - min(c.start, k.start)
            shorter = min(c.end - c.start, k.end - k.start)
            if (union > 0 and inter / union > 0.3) or (contain and shorter > 0 and inter / shorter > 0.5):
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept


def manual_bounds_check(start: float, end: float, duration: float, cfg: Config = CFG) -> str | None:
    """Return an error message for invalid manual boundaries, or None."""
    if any(map(lambda v: v is None or (isinstance(v, float) and math.isnan(v)), (start, end))):
        return "Start and end are required."
    if start < 0:
        return "Start can't be before 0:00."
    if end > duration + 0.05:
        return f"End is past the end of the source ({fmt_tc(duration)})."
    if end <= start:
        return "Start must be before end."
    if end - start < cfg.min_clip_s:
        return f"Clip must be at least {cfg.min_clip_s:.0f}s."
    if end - start > cfg.max_clip_s + 1e-6:
        return f"Clip is {end - start:.1f}s; the export limit is {cfg.max_clip_s:.0f}s."
    return None
