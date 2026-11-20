#!/usr/bin/env python3
"""Generate the two SYNTHETIC test samples used in TEST_REPORT.md (no real footage, no real speech).

  samples/out/synthetic_gaming_3m.mp4      3:00, 1920x1080@30, gameplay + facecam box,
                                           PLAY flash at 1:36, loud reaction 1:40–1:48, small bump 2:30
  samples/out/synthetic_gaming_chat.json   chat log: ~1.5 msgs/5 s baseline, burst 1:41–1:55, bump 2:31
  samples/out/synthetic_talk_2m30.mp4      2:30 talking-head stand-in; speech-like audio during each
                                           transcript cue; the relevant answer (1:09–1:41) is ~20 dB QUIETER
  samples/out/synthetic_talk_2m30.srt      the authored transcript for that audio (stand-in for Whisper)

Run:  python samples/make_samples.py
"""
from __future__ import annotations

import json
import random
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FONT = (HERE.parent / "clipdesk" / "static" / "fonts" / "FiraSans-Bold.ttf").as_posix()
SR = 48000


def write_wav(path: Path, x: np.ndarray):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def db(v):
    return 10 ** (v / 20)


def brown(n, rng):
    w = rng.standard_normal(n)
    b = np.cumsum(w)
    b -= np.convolve(b, np.ones(4801) / 4801, mode="same")
    return b / (np.abs(b).max() + 1e-9)


def gaming():
    rng = np.random.default_rng(7)
    D = 180
    n = D * SR
    t = np.arange(n) / SR
    a = brown(n, rng) * db(-30) * (1 + 0.15 * np.sin(2 * np.pi * t / 23))     # game ambience
    a += 0.02 * np.sin(2 * np.pi * 110 * t) * (np.sin(2 * np.pi * t / 7) > 0.6)  # periodic music-ish hum
    for k in range(3):                                                         # the PLAY: three beeps at 1:36
        s = int((96 + 0.4 * k) * SR)
        e = s + int(0.2 * SR)
        a[s:e] += db(-14) * np.sin(2 * np.pi * 880 * t[s:e])
    s, e = int(100 * SR), int(108 * SR)                                        # the REACTION 1:40–1:48
    env = np.minimum(1, np.minimum((t[s:e] - 100) / 0.3, (108 - t[s:e]) / 1.5))
    a[s:e] += db(-8) * env * (rng.standard_normal(e - s) * 0.6 + np.sin(2 * np.pi * 220 * t[s:e]) * 0.4)
    s, e = int(150 * SR), int(153 * SR)                                        # small bump 2:30
    a[s:e] += db(-20) * rng.standard_normal(e - s)
    write_wav(OUT / "_gaming.wav", a)
    vf = ("[0:v]format=yuv420p[g];[1:v]scale=420:316,format=yuv420p[f];"
          "[g][f]overlay=1440:700,drawbox=x=1436:y=696:w=428:h=324:color=0xff8fd8:t=6,"
          f"drawtext=fontfile='{FONT}':text='FACECAM':x=1460:y=712:fontsize=34:fontcolor=white,"
          "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='between(t,96,96.6)',"
          f"drawtext=fontfile='{FONT}':text='PLAY':x=(w-tw)/2:y=120:fontsize=140:fontcolor=yellow:enable='between(t,95.8,99)',"
          f"drawtext=fontfile='{FONT}':text='REACTION':x=1440:y=630:fontsize=56:fontcolor=0xff8fd8:enable='between(t,100,108)',"
          f"drawtext=fontfile='{FONT}':text='SYNTHETIC GAMING SAMPLE  %{{pts\\:hms}}':x=30:y=30:fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={D}",
                    "-f", "lavfi", "-i", f"mandelbrot=s=420x316:r=30",
                    "-i", str(OUT / "_gaming.wav"), "-filter_complex", vf, "-map", "[v]", "-map", "2:a", "-t", str(D),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "128k",
                    str(OUT / "synthetic_gaming_3m.mp4")], check=True)
    random.seed(7)
    msgs = []
    base_lines = ["lol", "gg", "nice", "what game is this", "hi chat", "o7", "first time here", "this map", "ok", "lmao"]
    for w in range(D // 5):
        for _ in range(np.random.default_rng(w).poisson(1.5)):
            msgs.append({"t": round(w * 5 + random.random() * 5, 2), "text": random.choice(base_lines)})
    hype = ["KEKW", "NO WAY", "CLIP IT", "W", "WWWW", "OMEGALUL", "no way he hit that", "INSANE", "clip it!!!", "PogChamp"]
    for _ in range(46):
        msgs.append({"t": round(101 + random.random() * 14, 2), "text": random.choice(hype)})
    for _ in range(5):
        msgs.append({"t": round(151 + random.random() * 4, 2), "text": random.choice(["lol", "W", "nice"])})
    msgs.sort(key=lambda m: m["t"])
    (OUT / "synthetic_gaming_chat.json").write_text(json.dumps(msgs, indent=0))
    (OUT / "_gaming.wav").unlink()


TALK = [  # (start, end, speaker, text, level_db)
    (1.0, 5.2, "Q", "Welcome back to the show, I'm really glad you could make it today.", -12),
    (5.8, 11.5, "A", "Thanks for having me. It's been a busy year with the new book and the tour.", -12),
    (12.2, 18.0, "Q", "Let's talk about the business podcast you launched, how did that start?", -12),
    (18.6, 27.0, "A", "Honestly it started as a hobby. I recorded episodes in my car between meetings.", -12),
    (27.6, 34.5, "A", "Then the audience grew and suddenly brands wanted to sponsor it, which was wild.", -11),
    (35.0, 41.8, "Q", "Your listeners love the energy. Is that natural or is it a performance?", -12),
    (42.4, 50.0, "A", "It's natural. I get loud when I'm excited, my family will tell you that!", -6),
    (50.6, 58.0, "A", "We once did a live show and I screamed so loud the microphone clipped.", -5),
    (58.6, 64.4, "Q", "Okay, I have to ask about something you rarely talk about.", -12),
    (65.0, 68.6, "Q", "What happened with your first business?", -12),
    (69.2, 76.0, "A", "My first company was a little coffee cart outside the train station.", -32),
    (76.6, 84.4, "A", "I put all my savings into it, and it went under after eight months.", -32),
    (85.0, 91.6, "A", "I was embarrassed for a long time, I didn't tell anyone for years.", -33),
    (92.2, 100.8, "A", "But what I learned was that you have to talk to customers before you spend a single dollar.", -32),
    (101.4, 106.0, "Q", "That's a powerful lesson. Do you still drink coffee?", -12),
    (106.6, 112.0, "A", "Every single morning, but I let someone else run the cart now.", -11),
    (112.6, 120.4, "Q", "Before we go, what are you working on next?", -12),
    (121.0, 130.0, "A", "A short course for first time founders, it launches in the spring.", -12),
    (130.6, 138.0, "Q", "We'll put the link in the description. Thank you so much for coming on.", -12),
    (138.6, 143.0, "A", "Thank you, this was fun.", -12),
]


def talk():
    rng = np.random.default_rng(3)
    D = 150
    n = D * SR
    t = np.arange(n) / SR
    a = rng.standard_normal(n) * db(-60)
    for s0, e0, spk, text, lvl in TALK:
        s, e = int(s0 * SR), int(e0 * SR)
        tt = t[s:e] - s0
        f0 = 120 if spk == "A" else 190
        syl = 0.5 * (1 + np.sin(2 * np.pi * 4.2 * tt + rng.random() * 6)) ** 2        # syllable rhythm
        voiced = sum(np.sin(2 * np.pi * f0 * k * tt) / k for k in range(1, 8))      # harmonic buzz
        noise = rng.standard_normal(e - s) * 0.25
        env = np.minimum(1, np.minimum(tt / 0.08, (e0 - s0 - tt) / 0.08))
        sig = (voiced * 0.35 + noise) * syl * env
        a[s:e] += db(lvl) * sig / (np.abs(sig).max() + 1e-9)
    write_wav(OUT / "_talk.wav", a)
    srt = []
    for i, (s0, e0, spk, text, lvl) in enumerate(TALK, 1):
        f = lambda x: f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{int(x % 60):02d},{int(round((x % 1) * 1000)):03d}"  # noqa
        srt.append(f"{i}\n{f(s0)} --> {f(e0)}\n{text}\n")
    (OUT / "synthetic_talk_2m30.srt").write_text("\n".join(srt))
    vf = ("color=c=0x2b3a55:s=1920x1080:r=30,format=yuv420p,"
          "drawbox=x=1050:y=180:w=420:h=720:color=0x8a6a50:t=fill,"           # 'body'
          "drawbox=x=1130:y=120:w=260:h=300:color=0xe0b090:t=fill,"           # 'head' (off-centre at x≈0.66)
          f"drawtext=fontfile='{FONT}':text='GUEST':x=1185:y=950:fontsize=60:fontcolor=white,"
          f"drawtext=fontfile='{FONT}':text='HOST':x=180:y=950:fontsize=60:fontcolor=white@0.5,"
          f"drawtext=fontfile='{FONT}':text='SYNTHETIC TALK SAMPLE  %{{pts\\:hms}}':x=30:y=30:fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-i", str(OUT / "_talk.wav"),
                    "-map", "0:v", "-map", "1:a", "-t", str(D), "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "128k", str(OUT / "synthetic_talk_2m30.mp4")], check=True)
    (OUT / "_talk.wav").unlink()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    gaming()
    talk()
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:34s} {p.stat().st_size / 1e6:6.1f} MB")
