#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bouzid FM — Google (Gemini) Text-to-Speech helper.

Turns text into an MP3 (or WAV) using the Gemini TTS API — the same models
exposed in Google AI Studio ("Generate speech"). Free-tier friendly.

Get an API key (free): https://aistudio.google.com/apikey

    export GEMINI_API_KEY=...
    python3 scripts/google_tts.py --text "مستمعي بوزيد إف إم" --out /tmp/test.mp3
    echo "نص طويل" | python3 scripts/google_tts.py --out bulletin.mp3 --voice Kore

No third-party deps — stdlib urllib + wave, plus ffmpeg for MP3 output.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import wave

API = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"  # latest, most expressive TTS
DEFAULT_VOICE = "Kore"  # firm female news-anchor voice; supports Arabic


class RateLimitExceeded(Exception):
    """Raised when the configured Google TTS daily cap is reached."""


def _throttle():
    """Pace Google TTS calls to respect the free-tier limit (default 3 req/min)
    so the key never gets rate-limited or banned. State is shared across
    processes via a lock + timestamp file, so the spacing holds even when
    several `bouzid` commands (news, cook, jingles) run back-to-back.

    Env knobs:
      GOOGLE_TTS_RPM           requests per minute (default 15)
      GOOGLE_TTS_MIN_INTERVAL  hard minimum seconds between calls (optional)
      GOOGLE_TTS_DAILY_CAP     max calls per day, 0 = unlimited (default 0)
      BOUZID_STATE_DIR         throttle state dir (default ~/.bouzid)
    """
    import datetime
    import fcntl
    import time
    try:
        rpm = float(os.environ.get("GOOGLE_TTS_RPM", "15") or 3)
    except ValueError:
        rpm = 3.0
    interval = 60.0 / rpm if rpm > 0 else 0.0
    mi = os.environ.get("GOOGLE_TTS_MIN_INTERVAL", "").strip()
    if mi:
        try:
            interval = max(interval, float(mi))
        except ValueError:
            pass
    try:
        cap = int(os.environ.get("GOOGLE_TTS_DAILY_CAP", "0") or 0)
    except ValueError:
        cap = 0
    if interval <= 0 and cap <= 0:
        return
    state_dir = (os.environ.get("BOUZID_STATE_DIR")
                 or os.path.expanduser("~/.bouzid"))
    try:
        os.makedirs(state_dir, exist_ok=True)
    except OSError:
        return
    lock_path = os.path.join(state_dir, "google_tts.lock")
    state_path = os.path.join(state_dir, "google_tts_state.json")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            st = {}
            if os.path.exists(state_path):
                try:
                    with open(state_path) as f:
                        st = json.load(f)
                except (ValueError, OSError):
                    st = {}
            today = datetime.date.today().isoformat()
            if st.get("day") != today:
                st["day"], st["count"] = today, 0
            if cap > 0 and st.get("count", 0) >= cap:
                raise RateLimitExceeded(
                    "Google TTS daily cap of %d reached" % cap)
            if interval > 0:
                wait = (float(st.get("last", 0)) + interval) - time.time()
                if wait > 0:
                    sys.stderr.write(
                        "  [rate-limit] pacing Google TTS: waiting %.0fs "
                        "(%.1f req/min)\u2026\n" % (wait, rpm))
                    sys.stderr.flush()
                    time.sleep(wait)
            st["last"] = time.time()
            st["count"] = st.get("count", 0) + 1
            with open(state_path, "w") as f:
                json.dump(st, f)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def synthesize(text, out_path, model=None, voice=None, api_key=None):
    api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
    model = model or os.environ.get("GEMINI_TTS_MODEL", DEFAULT_MODEL)
    voice = voice or os.environ.get("GEMINI_TTS_VOICE", DEFAULT_VOICE)

    _throttle()  # respect the free-tier rate limit before every call

    payload = dict(
        contents=[dict(parts=[dict(text=text)])],
        generationConfig=dict(
            responseModalities=["AUDIO"],
            speechConfig=dict(voiceConfig=dict(
                prebuiltVoiceConfig=dict(voiceName=voice))),
        ),
    )
    url = API + "/" + model + ":generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("x-goog-api-key", api_key)
    req.add_header("Content-Type", "application/json")
    import time as _time
    import re as _re
    data = None
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 429 = rate limited, 503 = overloaded: wait and retry.
            if e.code in (429, 503) and attempt < max_attempts:
                wait = 0.0
                m = _re.search(r'retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
                if not m:
                    m = _re.search(r'retry in (\d+(?:\.\d+)?)s', body)
                if m:
                    wait = float(m.group(1))
                wait = max(wait, 20.0) + 2.0  # buffer past the per-minute window
                sys.stderr.write(
                    "  rate limited (HTTP %s); waiting %.0fs then retrying (%d/%d)...\n"
                    % (e.code, wait, attempt, max_attempts - 1))
                sys.stderr.flush()
                _time.sleep(wait)
                continue
            sys.exit("Gemini API HTTP %s: %s" % (e.code, body))
        except urllib.error.URLError as e:
            sys.exit("Cannot reach Gemini API: %s" % e)
    if data is None:
        sys.exit("Gemini API failed after %d attempts (rate limit)." % max_attempts)

    try:
        parts = data["candidates"][0]["content"]["parts"]
        part = next(p for p in parts if "inlineData" in p)
    except (KeyError, IndexError, StopIteration):
        sys.exit("No audio returned. Raw response: " + json.dumps(data)[:500])

    mime = part["inlineData"].get("mimeType", "audio/L16;rate=24000")
    rate = 24000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass
    pcm = base64.b64decode(part["inlineData"]["data"])

    wav_path = out_path if out_path.endswith(".wav") else out_path + ".wav"
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)

    if out_path.endswith(".mp3"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
                        "-codec:a", "libmp3lame", "-qscale:a", "2", out_path],
                       check=True)
        os.remove(wav_path)
        print("Wrote", out_path)
    else:
        print("Wrote", wav_path)


def main():
    ap = argparse.ArgumentParser(description="Gemini TTS -> audio file")
    ap.add_argument("--text", help="text to speak")
    ap.add_argument("--file", help="read text from a file")
    ap.add_argument("--out", default="/tmp/bouzid_tts.mp3", help=".mp3 or .wav")
    ap.add_argument("--voice", help="prebuilt voice name (default Kore)")
    ap.add_argument("--model", help="Gemini TTS model id")
    args = ap.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("No text provided (use --text, --file, or stdin).")
    synthesize(text, args.out, model=args.model, voice=args.voice)


if __name__ == "__main__":
    main()
