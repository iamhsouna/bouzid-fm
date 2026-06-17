#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — Lyria 3 music generator (Google Gemini API).

Generates short, broadcast-ready music from a text prompt using Google
DeepMind's Lyria 3 model through the Gemini API (the standard generateContent
method). `lyria-3-clip-preview` returns a 30-second 48 kHz stereo MP3 — ideal
for jingle beds and station-imaging music.

This is the Google-native music option for the jingle studio. It needs a PAID
Gemini API key (Lyria is not on the free tier).

    python3 scripts/lyria_music.py --prompt "warm uplifting Tunisian radio bed, oud + light percussion" --out bed.mp3

Env: GEMINI_API_KEY, LYRIA_MODEL (default lyria-3-clip-preview), GEMINI_API_URL.
"""
import argparse
import base64
import json
import os
import sys
import time as _time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class RateLimitExceeded(Exception):
    """Raised when the configured Lyria daily cap is reached."""


def _env(key, default=""):
    val = os.environ.get(key)
    if val not in (None, ""):
        return val
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return default


def _throttle():
    """Pace Lyria 3 calls to respect API limits (default 3 req/min).

    Uses the same lock/state mechanism as google_tts.py so throttling is shared
    across processes. Env knobs:
      LYRIA_RPM           requests per minute (default 3)
      LYRIA_MIN_INTERVAL  hard minimum seconds between calls (optional)
      LYRIA_DAILY_CAP     max calls per day, 0 = unlimited (default 0)
      BOUZID_STATE_DIR    throttle state dir (default ~/.bouzid)
    """
    import datetime
    import fcntl
    try:
        rpm = float(os.environ.get("LYRIA_RPM", "3") or 3)
    except ValueError:
        rpm = 3.0
    interval = 60.0 / rpm if rpm > 0 else 0.0
    mi = os.environ.get("LYRIA_MIN_INTERVAL", "").strip()
    if mi:
        try:
            interval = max(interval, float(mi))
        except ValueError:
            pass
    try:
        cap = int(os.environ.get("LYRIA_DAILY_CAP", "0") or 0)
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
    lock_path = os.path.join(state_dir, "lyria.lock")
    state_path = os.path.join(state_dir, "lyria_state.json")
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
                    "Lyria daily cap of %d reached" % cap)
            if interval > 0:
                wait = (float(st.get("last", 0)) + interval) - _time.time()
                if wait > 0:
                    sys.stderr.write(
                        "  [rate-limit] pacing Lyria: waiting %.0fs "
                        "(%.1f req/min)\u2026\n" % (wait, rpm))
                    sys.stderr.flush()
                    _time.sleep(wait)
            st["last"] = _time.time()
            st["count"] = st.get("count", 0) + 1
            with open(state_path, "w") as f:
                json.dump(st, f)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _find_audio(data):
    """Pull the first inline audio blob out of a generateContent response."""
    cands = data.get("candidates") or []
    if not cands:
        return None
    parts = (cands[0].get("content", {}) or {}).get("parts", []) or []
    for p in parts:
        if not isinstance(p, dict):
            continue
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            return inline["data"]
    return None


def generate(prompt, out, model=None, instrumental=True, timeout=300):
    """Generate a music clip from `prompt` into `out` (mp3). Returns the path,
    or None on any failure (missing key, model error, no audio in response).

    Retries on HTTP 429/503 (rate limited) and on empty responses, with
    exponential backoff, so transient quota hiccups don't fail the job.
    """
    api_key = _env("GEMINI_API_KEY").strip()
    if not api_key:
        sys.stderr.write("Lyria: GEMINI_API_KEY is not set\n")
        return None
    model = (model or _env("LYRIA_MODEL", "lyria-3-clip-preview").strip()
             or "lyria-3-clip-preview")
    base = _env("GEMINI_API_URL",
                "https://generativelanguage.googleapis.com/v1beta/models")
    text = prompt.strip()
    if instrumental:
        text = ("Instrumental only, no vocals, no lyrics, no spoken words. "
                + text)
    payload = {"contents": [{"parts": [{"text": text}]}]}
    url = base.rstrip("/") + "/" + model + ":generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("x-goog-api-key", api_key)
    req.add_header("Content-Type", "application/json")

    max_attempts = 6
    last_err = None
    for attempt in range(1, max_attempts + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last_err = "HTTP %d" % e.code
            if e.code in (429, 503) and attempt < max_attempts:
                wait = 20.0
                import re as _re
                m = _re.search(r'retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
                if not m:
                    m = _re.search(r'retry in (\d+(?:\.\d+)?)s', body)
                if m:
                    wait = float(m.group(1))
                wait = max(wait, 10.0) + 2.0
                sys.stderr.write(
                    "  rate limited (HTTP %d); waiting %.0fs then retrying "
                    "(%d/%d)...\n" % (e.code, wait, attempt, max_attempts - 1))
                sys.stderr.flush()
                _time.sleep(wait)
                continue
            if e.code in (429, 503):
                sys.stderr.write("Lyria still rate limited after %d attempts.\n"
                                 % max_attempts)
                return None
            sys.stderr.write("Lyria returned HTTP %d: %s\n" % (e.code, body))
            return None
        except Exception as e:
            last_err = str(e)
            if attempt < max_attempts:
                wait = min(10 * attempt, 60)
                sys.stderr.write(
                    "  Lyria request failed (%s); retrying in %ds "
                    "(%d/%d)...\n" % (e, wait, attempt, max_attempts - 1))
                sys.stderr.flush()
                _time.sleep(wait)
                continue
            sys.stderr.write("Lyria request failed after %d attempts: %s\n"
                             % (max_attempts, e))
            return None

        b64 = _find_audio(data)
        if b64:
            break
        last_err = "no audio in response"
        if attempt < max_attempts:
            wait = min(15 * attempt, 60)
            sys.stderr.write(
                "  Lyria returned no audio (model/quota/access?); "
                "retrying in %ds (%d/%d)...\n"
                % (wait, attempt, max_attempts - 1))
            sys.stderr.flush()
            _time.sleep(wait)
            continue
        sys.stderr.write("Lyria returned no audio after %d attempts.\n"
                         % max_attempts)
        return None

    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        sys.stderr.write("Lyria audio decode failed (%s)\n" % e)
        return None
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(raw)
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate music with Lyria 3")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--with-vocals", action="store_true",
                    help="allow vocals/lyrics (default: instrumental bed)")
    args = ap.parse_args()
    out = generate(args.prompt, args.out, model=args.model,
                   instrumental=not args.with_vocals)
    if not out:
        print("✗ Lyria generation failed")
        return 1
    print("✓ wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
