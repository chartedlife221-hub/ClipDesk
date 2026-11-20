import json

import numpy as np
import pytest

from clipdesk import captions, detect, ingest, score, transcribe
from clipdesk.config import Settings


# ------------------------------------------------------------------ URLs
@pytest.mark.parametrize("url,origin,kind", [
    ("https://www.youtube.com/live/hwSg39yIBGU", "youtube", "video"),
    ("youtube.com/watch?v=hwSg39yIBGU&t=30", "youtube", "video"),
    ("https://youtu.be/hwSg39yIBGU", "youtube", "video"),
    ("https://www.twitch.tv/videos/2245123456", "twitch", "video"),
    ("https://www.twitch.tv/somestreamer", "twitch", "channel"),
    ("https://clips.twitch.tv/FunnyClipSlug", "twitch", "video"),
])
def test_valid_urls(url, origin, kind):
    v = ingest.validate_url(url)
    assert v["origin"] == origin and v["kind"] == kind


@pytest.mark.parametrize("url", ["", "ftp://youtube.com/watch?v=hwSg39yIBGU", "https://vimeo.com/123",
                                 "https://www.youtube.com/watch?v=short", "https://user:pw@youtube.com/watch?v=hwSg39yIBGU",
                                 "https://www.youtube.com:8443/watch?v=hwSg39yIBGU", "not a url at all",
                                 "https://evil.example/youtube.com/watch?v=hwSg39yIBGU", "file:///etc/passwd"])
def test_invalid_urls(url):
    with pytest.raises(ingest.SourceURLError):
        ingest.validate_url(url)


def test_ytdlp_classification():
    assert ingest.classify_ytdlp_error("ERROR: [youtube] x: This live event will begin in 3 hours.") == "live_waiting"
    assert ingest.classify_ytdlp_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access") == "inaccessible"
    assert ingest.classify_ytdlp_error("ERROR: [youtube] x: Video unavailable") == "inaccessible"
    assert ingest.classify_ytdlp_error("ERROR: Unable to connect to proxy") == "failed"
    assert ingest.classify_info({"live_status": "is_live"})[0] == "live_waiting"
    assert ingest.classify_info({"live_status": "post_live"})[0] == "live_waiting"
    assert ingest.classify_info({"live_status": "was_live"})[0] == "ready_to_download"
    assert ingest.classify_info({"availability": "subscriber_only"})[0] == "inaccessible"


# ------------------------------------------------------------------ chat + transcripts
def test_chat_formats():
    j = json.dumps([{"t": 3.5, "text": "hi"}, {"time": "0:10", "message": "W"}]).encode()
    assert [m["t"] for m in ingest.parse_chat(j)] == [3.5, 10.0]
    c = b"time,user,message\n00:01:05,a,KEKW\n7,b,lol\n"
    assert [m["t"] for m in ingest.parse_chat(c)] == [7.0, 65.0]
    yt = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "4200", "actions": [{"addChatItemAction": {
        "item": {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "no way"}]}}}}}]}}).encode()
    assert ingest.parse_chat(yt) == [{"t": 4.2, "text": "no way"}]
    tw = json.dumps({"comments": [{"content_offset_seconds": 12, "message": {"body": "PogChamp"}}]}).encode()
    assert ingest.parse_chat(tw) == [{"t": 12.0, "text": "PogChamp"}]
    with pytest.raises(ValueError):
        ingest.parse_chat(b"user,message\na,b\n")


def test_srt_and_vtt():
    srt = b"1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:03,500 --> 00:00:05,000\nGeneral Kenobi?\n"
    tr = transcribe.parse_caption_file(srt, "a.srt")
    assert tr["word_timing"] == "estimated" and tr["words"][0]["w"] == "Hello"
    assert [s["text"] for s in transcribe.sentences(tr)] == ["Hello there.", "General Kenobi?"]
    vtt = (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nwe<00:00:01.400><c> went</c><00:00:01.900><c> under</c>\n")
    tr = transcribe.parse_caption_file(vtt, "a.en.vtt")
    assert tr["word_timing"] == "measured"
    assert [(w["w"], w["s"]) for w in tr["words"]] == [("we", 1.0), ("went", 1.4), ("under", 1.9)]


def test_caption_lines_and_local_time():
    tr = {"words": [{"s": 10 + i * 0.4, "e": 10.3 + i * 0.4, "w": w} for i, w in enumerate(
        "this is a test of the caption chunker. second sentence here".split())]}
    lines = transcribe.caption_lines(tr, 10.0, 15.0)
    assert lines[0]["s"] == 10.0 and all(len(l["text"]) <= 32 for l in lines)
    assert any(l["text"].endswith("chunker.") for l in lines)  # sentence ends close a line
    local = captions.to_local([{"s": 9, "e": 11, "text": "a"}, {"s": 12, "e": 20, "text": "b"}, {"s": 30, "e": 31, "text": "c"}], 10, 15)
    assert local == [{"s": 0.0, "e": 1.0, "text": "a"}, {"s": 2.0, "e": 5.0, "text": "b"}]
    srt = captions.build_srt(local)
    assert "00:00:02,000 --> 00:00:05,000" in srt
    ass = captions.build_ass(local)
    assert "PlayResX: 1080" in ass and "Dialogue: 0,0:00:02.00,0:00:05.00" in ass


# ------------------------------------------------------------------ gaming detector
def test_gaming_short_recording():
    r = detect.gaming_detect(np.array([-30.0, -10.0]), None, 9.0, "")
    assert r.candidates == [] and "too short" in r.note


def test_gaming_zero_variance_and_missing_chat():
    r = detect.gaming_detect(np.full(60, -30.0), None, 300.0, "")
    assert r.candidates == [] and "flat" in r.note
    # flat audio but chat present: chat alone can drive candidates
    chat = [{"t": 150 + k * 0.2, "text": "KEKW"} for k in range(40)] + [{"t": k * 5.0, "text": "hi"} for k in range(60)]
    r = detect.gaming_detect(np.full(60, -30.0), chat, 300.0, "")
    assert r.candidates and abs(r.candidates[0].anchor - 152.5) < 6


def test_gaming_lookback_and_payoff():
    rms = np.full(120, -30.0) + np.sin(np.arange(120)) * 0.5
    rms[40] = -18   # the play
    rms[42:44] = -6  # reaction (210–220 s)
    r = detect.gaming_detect(rms, None, 600.0, "keep the reaction")
    c = r.candidates[0]
    assert c.start <= 200 and c.end >= 220 and c.end - c.start <= 45
    assert "no chat log" in c.reason and c.signals["chat_available"] is False


# ------------------------------------------------------------------ speech search
TALK = [(0, 4, "Thanks for having me on the show."), (5, 9, "Our podcast grew really fast!"),
        (10, 13, "What happened with your first business?"),
        (14, 20, "My first company was a coffee cart and it went under after eight months."),
        (21, 27, "What I learned was to talk to customers first."), (40, 44, "Anyway, back to the podcast.")]


def _tr():
    return {"words": [w for s, e, t in TALK for w in transcribe._spread([{"s": s, "e": e, "text": t}])],
            "word_timing": "estimated"}


def test_speech_finds_quiet_answer_with_question_and_payoff():
    r = detect.speech_search(_tr(), "Find the story about the first failed business and keep the lesson.", 50.0)
    c = r.candidates[0]
    assert c.strength == "strong"
    assert c.start <= 10.0 and c.end >= 27.0       # includes the question and the lesson
    assert "went under" in c.reason and "lesson" in c.reason


def test_speech_no_match_is_meaningful():
    r = detect.speech_search(_tr(), "Find the part about scuba diving", 50.0)
    assert r.candidates == [] and r.note.startswith("No strong match")


def test_speech_merges_overlaps():
    r = detect.speech_search(_tr(), "podcast", 50.0)
    spans = [(c.start, c.end) for c in r.candidates]
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            assert min(a[1], b[1]) - max(a[0], b[0]) <= 0.5


def test_manual_bounds():
    assert detect.manual_bounds_check(10, 5, 100) == "Start must be before end."
    assert "past the end" in detect.manual_bounds_check(90, 101, 100)
    assert "limit" in detect.manual_bounds_check(0, 60, 100)
    assert detect.manual_bounds_check(10, 40, 100) is None


# ------------------------------------------------------------------ model scoring
def test_rerank_fallbacks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = Settings()
    cands = detect.speech_search(_tr(), "first failed business", 50.0).candidates
    out, label = score.rerank(list(cands), "x", _tr(), s)
    assert label.startswith("Local ranker")
    s.model_provider = "anthropic"
    assert "no API key" in score.resolve_provider(s)[1]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")

    def boom(prompt):
        raise RuntimeError("401 bad key sk-test-secret")
    out, label = score.rerank(list(cands), "x", _tr(), s, call=boom)
    assert "call failed" in label and "sk-test" not in label

    def fake(prompt):
        return json.dumps([{"id": 0, "score": 0.9, "title": "Coffee cart went under", "reason": "invented words zzz qqq"}])
    out, label = score.rerank(list(cands), "x", _tr(), s, call=fake)
    assert label.startswith("Anthropic") and out[0].title == "Coffee cart went under"
    assert "[model]" not in out[0].reason  # ungrounded model reason is rejected
