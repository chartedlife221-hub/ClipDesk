#!/usr/bin/env bash
# Starts ClipDesk each time you open the codespace (skips if it's already running).
if curl -sf http://127.0.0.1:8765/api/health >/dev/null; then
  echo "ClipDesk is already running — open the Ports tab → ClipDesk (8765)."
  exit 0
fi
python run.py --no-browser --port 8765
