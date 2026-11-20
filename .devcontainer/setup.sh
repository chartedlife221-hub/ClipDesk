#!/usr/bin/env bash
# One-time setup when the codespace is created: FFmpeg + Python packages.
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg
pip install --quiet -r requirements.txt
python run.py --check
