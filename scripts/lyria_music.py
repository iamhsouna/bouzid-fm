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
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


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
    or None on any failure (missing key, model error, no audio in response)."""
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("Lyria request failed (%s)\n" % e)
        return None
    b64 = _find_audio(data)
    if not b64:
        sys.stderr.write("Lyria returned no audio (model/quota/access?)\n")
        return None
    try:
        raw = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
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
