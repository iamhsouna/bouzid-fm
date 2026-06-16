#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — ElevenLabs audio (sound effects + music).

Google has no simple text-to-SFX endpoint, so for broadcast sound effects /
stings we use ElevenLabs' Sound Effects API. Eleven Music is also wired up as
a commercially-cleared alternative for music beds.

  SFX:   POST https://api.elevenlabs.io/v1/sound-generation
  Music: POST https://api.elevenlabs.io/v1/music

Both return a binary MP3. Auth is the `xi-api-key` header.

    python3 scripts/elevenlabs_audio.py sfx   --text "clean radio whoosh sting" --out sting.mp3 --seconds 2
    python3 scripts/elevenlabs_audio.py music --prompt "warm Tunisian radio bed, oud" --out bed.mp3 --seconds 20

Env: ELEVENLABS_API_KEY, ELEVEN_SFX_MODEL, ELEVEN_MUSIC_MODEL.
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BASE = "https://api.elevenlabs.io"


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


def _key():
    return _env("ELEVENLABS_API_KEY").strip()


def _post(path, body, out, timeout=300):
    key = _key()
    if not key:
        sys.stderr.write("ElevenLabs: ELEVENLABS_API_KEY is not set\n")
        return None
    url = BASE + path
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "audio/mpeg")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:  # noqa: BLE001
        msg = ""
        try:
            msg = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write("ElevenLabs HTTP %s: %s\n" % (e.code, msg))
        return None
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("ElevenLabs request failed (%s)\n" % e)
        return None
    if not raw or len(raw) < 256:
        sys.stderr.write("ElevenLabs returned an empty/short response\n")
        return None
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(raw)
    return out


def sfx(text, out, duration=None, prompt_influence=0.4, loop=False,
        model_id=None, output_format="mp3_44100_128"):
    """Generate a sound effect / sting from text. duration 0.5..30s or None."""
    model_id = (model_id or _env("ELEVEN_SFX_MODEL", "eleven_text_to_sound_v2")
                or "eleven_text_to_sound_v2")
    body = {
        "text": text,
        "prompt_influence": float(prompt_influence),
        "loop": bool(loop),
        "model_id": model_id,
    }
    if duration:
        d = max(0.5, min(float(duration), 30.0))
        body["duration_seconds"] = d
    return _post("/v1/sound-generation?output_format=" + output_format,
                 body, out)


def music(prompt, out, seconds=None, instrumental=True, model_id=None,
          output_format="mp3_44100_128"):
    """Compose a music bed from a prompt. seconds 3..600 or None (auto)."""
    model_id = (model_id or _env("ELEVEN_MUSIC_MODEL", "music_v1")
                or "music_v1")
    body = {
        "prompt": prompt,
        "model_id": model_id,
        "force_instrumental": bool(instrumental),
    }
    if seconds:
        ms = int(max(3.0, min(float(seconds), 600.0)) * 1000)
        body["music_length_ms"] = ms
    return _post("/v1/music?output_format=" + output_format, body, out)


def main():
    ap = argparse.ArgumentParser(description="ElevenLabs SFX + music")
    sub = ap.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("sfx", help="text to sound effect")
    s.add_argument("--text", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--seconds", type=float, default=None)
    s.add_argument("--influence", type=float, default=0.4)
    s.add_argument("--loop", action="store_true")

    m = sub.add_parser("music", help="text to music bed")
    m.add_argument("--prompt", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--seconds", type=float, default=None)
    m.add_argument("--with-vocals", action="store_true")

    args = ap.parse_args()
    if args.mode == "sfx":
        out = sfx(args.text, args.out, duration=args.seconds,
                  prompt_influence=args.influence, loop=args.loop)
    else:
        out = music(args.prompt, args.out, seconds=args.seconds,
                    instrumental=not args.with_vocals)
    if not out:
        print("✗ ElevenLabs generation failed")
        return 1
    print("✓ wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
