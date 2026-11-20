import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("CLIPDESK_DATA", str(d))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return d


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory):
    """12 s 640x360 H.264/AAC clip with a loud burst at 6–8 s."""
    p = tmp_path_factory.mktemp("media") / "tiny.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=12",
                    "-f", "lavfi", "-i", "anoisesrc=d=12:a=0.02:seed=1",
                    "-filter_complex", "[1:a]volume='if(between(t,6,8),25,1)':eval=frame[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(p)], check=True)
    return p
