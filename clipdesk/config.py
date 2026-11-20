"""All tunables and on-disk locations in one place.

Data layout (default root: ./clipdesk-data next to the project, override with CLIPDESK_DATA):

    clipdesk-data/
      clipdesk.sqlite        sources, candidates, clips, revisions, jobs (all state)
      settings.json          user settings (model provider, whisper model, defaults)
      sources/<source_id>/   original media, proxy, poster, transcript.json, chat.json
      renders/<clip_id>/     preview + final renders, keyed by revision
      exports/               ZIP bundles
      models/                faster-whisper model cache (first-run download)
      logs/clipdesk.log
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_root() -> Path:
    root = Path(os.environ.get("CLIPDESK_DATA", PROJECT_ROOT / "clipdesk-data")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("sources", "renders", "exports", "models", "logs", "tmp"):
        (root / sub).mkdir(exist_ok=True)
    return root


@dataclass
class Config:
    # --- export target (assignment p.3) ---
    out_w: int = 1080
    out_h: int = 1920
    fps: int = 30
    max_clip_s: float = 45.0
    min_clip_s: float = 3.0
    preview_w: int = 540
    preview_h: int = 960

    # --- gaming detector (appendix §6) ---
    window_s: float = 5.0
    baseline_s: float = 50 * 60.0        # rolling baseline ~50 min
    min_baseline_windows: int = 12       # below this the whole recording is the baseline
    w_audio: float = 0.40
    w_chat: float = 0.40
    w_hype: float = 0.20
    w_laugh: float = 0.00
    squash_center: float = 2.0
    gate: float = 0.35                   # minimum combined score for a gaming candidate
    pre_roll_max_s: float = 20.0         # how far back to look for the play/setup
    post_roll_max_s: float = 12.0        # how far forward to keep the reaction/payoff
    max_candidates: int = 10

    # --- speech search ---
    strong_match: float = 0.55           # normalised score needed to call a match "strong"
    weak_match: float = 0.25

    # --- transcription ---
    whisper_model: str = "base"          # small enough for an ordinary CPU; "small" is better
    whisper_compute: str = "int8"


@dataclass
class Settings:
    """User-editable settings persisted to settings.json."""
    whisper_model: str = "base"
    model_provider: str = "auto"         # auto | anthropic | openai | local
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4o-mini"
    default_layout: str = "speaker"      # speaker | fit | gameplay
    caption_font: str = "Fira Sans"
    caption_size: int = 76
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        p = data_root() / "settings.json"
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
                return cls(**known)
            except Exception:
                pass
        s = cls()
        s.save()
        return s

    def save(self) -> None:
        p = data_root() / "settings.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(p)


CFG = Config()
