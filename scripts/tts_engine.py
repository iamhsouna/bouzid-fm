#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — text-to-speech engine (Google-only).

Bouzid FM 2.0 speaks with a single, high-quality voice provider: Google Gemini
TTS. There is intentionally no other backend — the app is simple, clean and
Google-only. This thin wrapper exposes one stable entry point used everywhere
in the project:

    tts_engine.synthesize(text, out_path[, voice=...])

It delegates to scripts/google_tts.py (the raw Gemini TTS client with rate
limiting + retries) and lets every show keep the same simple call site.

Voices (all speak Arabic). Kore (female) is the station default. Pick any
voice below in Settings, or pass --voice on the CLI.

Env: GEMINI_API_KEY, GEMINI_TTS_MODEL, GEMINI_TTS_VOICE.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import google_tts  # noqa: E402

BACKEND = "google"

# (voice id, gender, short note). Female-first; Kore is the station default.
# Genders per Google's Gemini-TTS voice documentation.
GEMINI_VOICES = [
    # --- Female ---
    ("Kore", "female", "firm, warm — station default"),
    ("Aoede", "female", "breezy, light"),
    ("Leda", "female", "youthful, bright"),
    ("Zephyr", "female", "bright"),
    ("Autonoe", "female", "bright"),
    ("Callirrhoe", "female", "easy-going"),
    ("Despina", "female", "smooth"),
    ("Erinome", "female", "clear"),
    ("Gacrux", "female", "mature"),
    ("Laomedeia", "female", "upbeat"),
    ("Achernar", "female", "soft"),
    ("Pulcherrima", "female", "forward"),
    ("Sulafat", "female", "warm"),
    ("Vindemiatrix", "female", "gentle"),
    # --- Male ---
    ("Charon", "male", "informative"),
    ("Puck", "male", "upbeat"),
    ("Fenrir", "male", "excitable"),
    ("Orus", "male", "firm"),
    ("Enceladus", "male", "breathy"),
    ("Iapetus", "male", "clear"),
    ("Algenib", "male", "gravelly"),
    ("Algieba", "male", "smooth"),
    ("Achird", "male", "friendly"),
    ("Alnilam", "male", "firm"),
    ("Rasalgethi", "male", "informative"),
    ("Sadachbia", "male", "lively"),
    ("Sadaltager", "male", "knowledgeable"),
    ("Schedar", "male", "even"),
    ("Umbriel", "male", "easy-going"),
    ("Zubenelgenubi", "male", "casual"),
]

VOICE_GENDER = {v: g for v, g, _ in GEMINI_VOICES}


def synthesize(text, out_path, backend=None, voice=None, rate=None, pitch=None):
    """Synthesize `text` to `out_path` with Google Gemini TTS.

    `backend` is accepted only for call-site compatibility; the sole supported
    value is "google". `rate`/`pitch` are accepted but not used by Gemini TTS
    (tone is shaped later by voice_fx). Raises SystemExit with a clear message
    when GEMINI_API_KEY is missing.
    """
    if backend and backend.strip().lower() not in ("google", "gemini", ""):
        raise SystemExit(
            "Bouzid FM 2.0 is Google-only. Unsupported TTS backend: %r. "
            "Use 'google'." % backend)
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise SystemExit(
            "GEMINI_API_KEY is not set. Add it to .env or Settings → Voice "
            "to enable Google Gemini TTS.")
    return google_tts.synthesize(text, out_path, voice=voice)


def _print_voices():
    fem = [(v, n) for v, g, n in GEMINI_VOICES if g == "female"]
    mal = [(v, n) for v, g, n in GEMINI_VOICES if g == "male"]
    print("Google Gemini TTS voices (all speak Arabic):\n")
    print("  Female:")
    for v, n in fem:
        print("    %-16s %s" % (v, n))
    print("\n  Male:")
    for v, n in mal:
        print("    %-16s %s" % (v, n))


def main():
    ap = argparse.ArgumentParser(
        description="Bouzid FM 2.0 Google Gemini TTS")
    ap.add_argument("--text", help="text to synthesize")
    ap.add_argument("--out", help="output .wav/.mp3 path")
    ap.add_argument("--voice", help="Gemini voice (e.g. Kore, Leda, Charon)")
    ap.add_argument("--backend", default="google",
                    help="compatibility only; must be 'google'")
    ap.add_argument("--list-voices", action="store_true",
                    help="list available Gemini voices grouped by gender")
    args = ap.parse_args()

    if args.list_voices:
        _print_voices()
        return
    if not args.text or not args.out:
        ap.error("--text and --out are required (or use --list-voices)")
    synthesize(args.text, args.out, backend=args.backend, voice=args.voice)
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
