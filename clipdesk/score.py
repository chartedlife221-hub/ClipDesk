"""Optional model re-ranking of candidates.

Providers: Anthropic (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY), chosen by settings
`model_provider` (auto | anthropic | openai | local). Missing credentials or any API error
fall back to the local ranker, and the request records which ranker produced the order.
The model only sees the transcript excerpt + signal summary of each candidate, and its
title/reason are rejected unless they quote words that occur in that excerpt.
API keys are read from the environment only and never logged or stored.
"""
from __future__ import annotations

import json
import logging
import os
import re

from .config import Settings
from .detect import Candidate, norm
from .transcribe import words_between

log = logging.getLogger("clipdesk.score")

LOCAL_LABEL = "Local ranker (keyword + signal heuristics; no AI model)"


def resolve_provider(settings: Settings) -> tuple[str, str]:
    """Return (provider, label). provider ∈ {'anthropic','openai','local'}."""
    want = settings.model_provider
    if want in ("auto", "anthropic") and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", f"Anthropic {settings.anthropic_model}"
    if want in ("auto", "openai") and os.environ.get("OPENAI_API_KEY"):
        return "openai", f"OpenAI {settings.openai_model}"
    if want in ("anthropic", "openai"):
        return "local", f"{LOCAL_LABEL} — {want} selected but no API key in the environment"
    return "local", LOCAL_LABEL


def _call(provider: str, settings: Settings, prompt: str) -> str:
    import requests
    if provider == "anthropic":
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
            "content-type": "application/json"},
            json={"model": settings.anthropic_model, "max_tokens": 1500, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=60, headers={
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": settings.openai_model, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def rerank(cands: list[Candidate], request: str, transcript: dict | None, settings: Settings,
           call=None) -> tuple[list[Candidate], str]:
    provider, label = resolve_provider(settings)
    if provider == "local" or not cands:
        return cands, label
    items = []
    for i, c in enumerate(cands):
        text = " ".join(w["w"] for w in words_between(transcript or {}, c.start, c.end))[:1200]
        items.append({"id": i, "start": c.start, "end": c.end, "transcript": text, "signals": c.signals})
    prompt = (
        "You rank short-video clip candidates for a clipper. Request: " + json.dumps(request) + "\n"
        "Candidates (JSON): " + json.dumps(items) + "\n"
        "Return ONLY a JSON array, one object per candidate: {\"id\": int, \"score\": 0..1, "
        "\"title\": str (<=60 chars, reuse words from that candidate's transcript), "
        "\"reason\": str (<=200 chars, quote the transcript)}. Score 0 if it does not satisfy the request.")
    try:
        raw = (call or (lambda p: _call(provider, settings, p)))(prompt)
        m = re.search(r"\[.*\]", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
    except Exception as e:  # noqa: BLE001 — any failure falls back
        msg = str(e)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            if os.environ.get(k):
                msg = msg.replace(os.environ[k], "***")
        log.warning("model scoring failed (%s): %s", provider, msg[:200])
        return cands, f"{LOCAL_LABEL} — {label} call failed ({type(e).__name__})"
    by_id = {d.get("id"): d for d in data if isinstance(d, dict)}
    for i, c in enumerate(cands):
        d = by_id.get(i)
        if not d:
            continue
        tn = norm(items[i]["transcript"])
        try:
            s = float(d.get("score", 0))
        except (TypeError, ValueError):
            continue
        c.signals = {**c.signals, "model_score": round(max(0.0, min(1.0, s)), 3), "local_score": c.score}
        c.score = round(0.5 * c.score + 0.5 * max(0.0, min(1.0, s)), 3)
        title, reason = str(d.get("title", ""))[:64], str(d.get("reason", ""))[:220]
        grounded = lambda t: sum(1 for w in norm(t).split() if len(w) > 3 and w in tn) >= 2  # noqa: E731
        if title and grounded(title):
            c.title = title
        if reason and grounded(reason):
            c.reason = f"{reason} [model] · {c.reason}"
    cands.sort(key=lambda c: (-c.score, c.start))
    return cands, label
