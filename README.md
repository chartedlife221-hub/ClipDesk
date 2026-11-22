# ClipDesk: owner test build (v0.3.0, November 2026)

ClipDesk is a local clipping workspace. You import a stream replay or a talk, find a moment by describing it (or jump to a timestamp you already know), frame it 9:16, edit captions, approve it, and export a 1080×1920 MP4 or a ZIP of every approved clip.

Everything runs on your own machine. The server binds to `127.0.0.1` only. Nothing is published anywhere: you download the files and post them yourself.

---

## 0. Run it on GitHub Codespaces (no install)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/chartedlife221-hub/ClipDesk)

1. Click the badge (or on the repo: **Code → Codespaces → Create codespace on main**).
2. Wait for the first build. It installs FFmpeg and the Python packages, which takes a few minutes the first time.
3. ClipDesk starts by itself and opens in a new browser tab. If it doesn't, open the **Ports** tab and click the globe icon next to **ClipDesk (8765)**.
4. Only your logged-in GitHub account can open that address (the port is private by default). Keep it that way: ClipDesk has no login of its own.

Notes:

- Codespaces use your GitHub account's monthly allowance. Stop the codespace when you're done: **github.com/codespaces → ⋯ → Stop**. Your data stays in the codespace until you delete it.
- The first transcription downloads the Whisper model inside the codespace.
- YouTube sometimes blocks downloads from cloud servers ("Sign in to confirm you're not a bot"). ClipDesk shows that as *Inaccessible*. If it happens, upload the file instead, or run ClipDesk on your own computer.
- Rendering is slower on the default 2-core codespace; pick a 4-core machine type for faster exports.

## What's new in v0.3: Shorts caption styles

Open a clip and go to the **Style** tab.

| Preset | Look |
|---|---|
| Clean | White bold captions with an outline (the v0.2 look) |
| Pop | Uppercase, extra-bold; the word being spoken turns your highlight colour and grows |
| Karaoke | Words fill in as they're spoken: said = white, now = colour, upcoming = dimmed |
| Boxed | White text on a dark box, readable on any background |
| Headline | Tall condensed type (Anton), two words at a time, highlight on the spoken word |

- **Highlight colour:** five swatches or any custom colour.
- **Words on screen:** 1, 3 or the full line. Pop and Headline default to short bursts.
- **All capitals, size and height on screen.**
- **Logo:** upload one per creator (or a general one for clips without a creator), pick a corner, size and opacity. Logos are re-encoded to PNG by FFmpeg before use.
- **"Use this look for all new … clips"** saves the style and logo as that creator's default; every new clip, found or made by hand, starts with it.
- Word timing comes from the transcript. If you've edited a caption line, its timing is spread across the words instead.
- The live preview uses the same fonts, sizes, wrapping, outline, box and highlight timing as the export (checked frame against frame).
- The `.srt` in ZIP exports stays plain text; the `.ass` file carries the styling.

## What's new in v0.2

| Before (v0.1) | Now (v0.2) |
|---|---|
| Dark screen with one yellow accent, text-glyph buttons | New design: colour carries meaning (teal = start, coral = end, lilac = main action, green/rose = approve/reject), always paired with an icon or words; one SVG icon set |
| Plain grey trim bar | **Filmstrip trim**: thumbnails of the footage under big start/end timecodes, 44 px drag handles that work with a finger |
| Hand-trimmed edges could cut words | **Snap to pauses** (on by default, toggle under the trim bar): manual edges move to the nearest word edge, or the quietest moment if there's no transcript, never more than 1.2 s. The note under the bar says how far it moved |
| Off-centre speakers needed fixing on every clip | **Framing memory**: the crop you set is reused for every new clip from that recording |
| Desktop-only layout | **Phone layout**: bottom navigation, Find/Edit tabs, review bar docked at the bottom, 44 px touch targets, 16 px text |
| Approve/reject with no way back except a key | Every approve/reject toast has an **Undo** button |
| Jobs bar covered the page | Background work sits in a small **activity button**; tap it for progress, cancel and retry |
| — | Progress steps on each recording (Import, Transcript, Make clips, Approve, Export), loading states on every button, clearer error and empty-state messages, reduced-motion support |

Screenshots: `docs/screenshots/`.

## 1. First run on your own computer

**You need:** Python 3.10 or newer, and FFmpeg (which includes `ffprobe`).

| OS | Install FFmpeg |
|---|---|
| Windows | `winget install Gyan.FFmpeg`, then open a **new** terminal |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |

```bash
cd clipdesk
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

`python run.py` is the single start command on every OS. It:

- checks Python, FFmpeg/ffprobe and the packages
- picks the first free port from 8765
- starts the server and confirms `/api/health` answers
- prints the URL, host, data folder and log file, then opens the browser

| Command | What it does |
|---|---|
| `python run.py` | start (opens the browser) |
| `python run.py --no-browser` | start without opening a browser |
| `python run.py --port 9000` | prefer a different port |
| `python run.py --check` | dependency check only |
| `python run.py --stop` or Ctrl+C | stop |
| run `python run.py` again | restart; approvals, edits and renders are kept |

**First-run downloads.** The first transcription downloads the Whisper model chosen in *Setup* into `clipdesk-data/models/`:

- `base` (default): about 145 MB, fine on an ordinary CPU
- `small`: about 490 MB, better accuracy, slower

After that it runs offline. URL import needs network access to YouTube or Twitch.

**Optional AI ranking.** If `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set in the environment when you start the server, discovered candidates are re-ranked by that model. The results page names which provider ranked them. With no key, or if the call fails, the *Local ranker (keyword + signal heuristics; no AI model)* is used and labelled that way. Keys are never written to disk or logs.

## 2. Five-minute walkthrough (source → exported file)

1. **Add a creator** (left column on *Sources*), e.g. "Nadia – streamer".
2. **Add a source.** Either:
   - paste a YouTube or Twitch replay URL (optionally *only import part of a long stream*), or
   - drag a video file onto the upload box.

   The card shows creator, title, duration and status:

   | Status | Meaning |
   |---|---|
   | Importing | progress bar |
   | Ready | imported |
   | Live · awaiting replay | the stream is live or upcoming, or the replay is still processing |
   | Inaccessible | private, members-only, removed or region-locked |
   | Failed | the real error is shown, with **Retry** |
3. **Open the source.** Optionally add a **chat log** (YouTube/Twitch replay chat is fetched automatically on URL import) or a **transcript** (.srt/.vtt). Without faster-whisper, a transcript is required for speech search.
4. **Clip a moment you already know:** press **G**, type `12:05`, Enter. The player seeks there and a selection is placed around it. Adjust **Start/End** (type, use ±, **I**/**O** at the playhead, or drag the yellow handles). **P** replays just the selection. Then click **New clip from selection**. This works while a search is still running.
5. **Or find a moment:** press **/**, describe it, then **Find**. Examples:
   - gaming: "Find a surprising play and keep the reaction."
   - speech: "Find the story about the first failed business and keep the lesson."

   You get ranked results with source timecodes, *strong/weak* labels and a reason quoting the transcript or signals. If nothing matches well it says so ("No strong match: …").
6. **Edit** in the right column:
   - framing: *Speaker crop* (position slider; *Auto-center on face*), *Fit to frame*, or *Gameplay + facecam* (drag the pink facecam and blue action boxes on the player)
   - caption position, title, hashtags, and each caption line

   The 9:16 live preview updates as you edit. **V** renders a preview through the real export pipeline. Every change is saved as a new revision, and **U** or **Ctrl/⌘+Z** undoes it.
7. **Review:** **A** approves and **R** rejects. Decisions survive a refresh and a restart. The *Review queue* tab lists every clip across creators (**J/K** to move, **X** to tick for export).
8. **Export:** **E** renders the approved revision and downloads the MP4. *Export all approved (ZIP)* in the queue bundles, per clip:
   - the MP4
   - `.srt` and `.ass` captions
   - a `.txt` with title, hashtags and source timestamps
   - plus `manifest.json` and `manifest.csv` for the whole batch

Press **?** in the app for every shortcut. Shortcuts are ignored while you type in a field; Esc leaves the field.

## 3. Where things live

All under `clipdesk-data/` next to `run.py`. Set `CLIPDESK_DATA=/path` to move it.

| Path | Contents |
|---|---|
| `clipdesk.sqlite` | creators, sources, discovery requests, immutable candidates, clips, append-only revisions, review actions, jobs |
| `settings.json` | Whisper model, ranker, default framing, caption size |
| `sources/<id>/` | original media (server-named), proxy, poster, `transcript.json`, `chat.json`, loudness windows |
| `renders/<clip>/` | preview/final MP4 + ASS, named by revision and content hash |
| `exports/` | ZIP bundles (same approved set → same file) |
| `models/` | Whisper models |
| `logs/clipdesk.log` | server log |

## 4. Engineering decisions that affect setup or clip quality

- **Stack.** Python, Starlette/FastAPI, SQLite, FFmpeg, and a single-page browser UI with no build step.
  - Routes are plain Starlette routes. If FastAPI is installed (it's in `requirements.txt`) the app is a FastAPI instance; otherwise it runs on bare Starlette.
  - The build workspace had no FastAPI and no package network, so the Starlette path is the one that was exercised.
- **Jobs.** Two worker lanes, *analysis* (import/transcribe/discover) and *render* (preview/final/ZIP), so a manual clip renders while discovery runs.
  - Jobs are persisted with progress, failure detail, **cancel** (kills ffmpeg/yt-dlp) and **retry** (same job, attempt+1).
  - After a restart, interrupted jobs are re-queued. Outputs are keyed by content and written atomically, so retries never duplicate files.
- **Gaming discovery** follows appendix §6:
  - 5-second windows, each signal z-scored against the recording's own rolling ~50-minute baseline, `squash(z)=1/(1+e^-(z-2))`, `C = .4 audio + .4 chat + .2 hype (+0 laugh)`
  - recordings with fewer than 12 windows use whole-recording stats; recordings under 15 s say "too short"
  - zero variance gives z = 0 and a "flat" note; with no chat, scoring is loudness-only and labelled so
  - windows above the gate within 10 s of each other form one moment, so a play followed by a reaction is one clip
  - boundaries look back up to 20 s for the calm before the spike (the setup) and forward until the reaction decays; 45 s max, setup trimmed first
- **Speech discovery** searches the whole timestamped transcript, so loudness plays no part and a quiet answer can rank first.
  - The request is split into *find* and *keep* parts ("…and keep the lesson") and expanded with a small documented synonym list (e.g. *failed* ≈ "went under").
  - Your own words count fully, synonyms count 0.7, and rare terms weigh more.
  - Clips extend back to an interview question (one ending in "?") just before the answer and forward to the requested payoff. A window that ends on a question takes its answer. Boundaries snap to sentence edges.
  - Overlapping suggestions are merged.
  - Titles are transcript sentences. Reasons quote the transcript and timestamp each matched term, so they're grounded in the media.
- **Revisions.** Candidates (original suggestions) are never modified. Each edit creates a revision, stored in **source** seconds. Captions are shifted to clip-local time only at render, so trimming never desyncs them.
  - Final renders always come from the **approved** revision. Editing after approval shows a warning until you approve again.
- **Output.** 1080×1920, 30 fps, H.264 (CRF 20, veryfast, ≤12 Mb/s) + AAC 160 kb/s 48 kHz stereo, `+faststart`, ≤45 s.
  - Captions are burned with libass in the bundled Fira Sans Bold (OFL), at 76 px on the 1080-wide frame, with 90 px side margins and balanced two-line wrapping.
  - The browser live preview uses the same crop maths, font, size and wrap rule. *Render preview* runs the identical ffmpeg graph at 540×960.
- **Safety.**
  - Loopback bind by default.
  - Only YouTube/Twitch URLs are accepted: one video, no credentials, no custom ports. Twitch channel pages are marked *awaiting replay*.
  - Uploads must have a video extension and pass `ffprobe`.
  - All file paths are server-generated from validated IDs, and media paths never reach the browser.

## 5. Tests

```bash
pip install pytest requests
python -m pytest -q          # 34 tests: units + full service flow + live HTTP server (needs ffmpeg)
python samples/make_samples.py   # regenerates the synthetic samples used in TEST_REPORT.md
```

## 6. Not in this build (paused for owner feedback)

These are deliberately left out:

- live monitoring
- public hosting or login
- auto-publishing
- earnings tracking
- multi-speaker reframing

Appendix figures (≤15 min per 1-hour VOD on GPU, ≥60 % approval, <$0.10 per VOD) are measurement targets. They haven't been measured yet; see TEST_REPORT.md.
