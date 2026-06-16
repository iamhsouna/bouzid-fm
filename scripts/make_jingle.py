#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — jingle studio (Google-first, with ElevenLabs SFX).

Four ways to build a short, broadcast-ready station jingle:

  draft      Write (only) the Arabic station-ID script with Gemini and print
             it, so you can REVIEW and edit it before any audio is made.
  voiced     Speak a script (yours, or a drafted one) over a music bed, with
             an opening sting, auto-ducked broadcast mix.
  voiceonly  Speak a script DRY — just the clean voice, no music bed.
  sfx        A sound-effect sting, no voice.

The voice script is never auto-generated at build time: you either pass it
verbatim with --script, or draft it first (the `draft` step) and review it.

Music bed source (voiced):  assets (mastered, default) | lyria | eleven
SFX source (sfx):           assets (mastered montage, default) | eleven

Jingles land in $MUSIC_DIR/jingles (so `bouzid rescan` imports them) and a
copy is dropped in assets/jingles for the news mixer. Add --air to cut one
onto the stream now.

Examples:
  python3 scripts/make_jingle.py draft --prompt "ترحيب صباحيّ"
  python3 scripts/make_jingle.py voiced --script "بوزيد إف إم، صوت سيدي بوزيد" --voice Kore
  python3 scripts/make_jingle.py voiced --script "..." --bed-source lyria --bed-prompt "warm oud radio bed"
  python3 scripts/make_jingle.py voiceonly --script "..." --voice Orus --name station_dry
  python3 scripts/make_jingle.py sfx --source eleven --sfx-prompt "clean radio whoosh sting" --name whoosh
"""
import argparse
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import tts_engine   # noqa: E402  (Google Gemini TTS wrapper)
import audio_mix    # noqa: E402  (ffmpeg mixer: build / concat)

try:
    import lyria_music   # noqa: E402  (Google Lyria 3 music)
except Exception:        # noqa: BLE001
    lyria_music = None
try:
    import elevenlabs_audio   # noqa: E402  (ElevenLabs SFX + music)
except Exception:            # noqa: BLE001
    elevenlabs_audio = None

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus")
DRAFT_PATH = os.path.join(ROOT, ".jingle_draft.txt")


# --------------------------------------------------------------------------- #
#  Env + paths
# --------------------------------------------------------------------------- #
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


def music_root():
    md = _env("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    return md


def jingles_dir():
    d = os.path.join(music_root(), "jingles")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
#  Mastered-asset resolution (assets/<set>/<kind>)
# --------------------------------------------------------------------------- #
def _first(d):
    if not d or not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d)
                   if f.lower().endswith(AUDIO_EXT) and not f.startswith("."))
    return os.path.join(d, files[0]) if files else None


def _kind_dir(sset, kind):
    if sset == "station":
        return os.path.join(ROOT, "assets", kind)
    return os.path.join(ROOT, "assets", sset, kind)


def _asset(sset, kind):
    """A mastered asset file for a set ('station' or a show slug).
    kind in {intro, outro, beds, transition}."""
    return _first(_kind_dir(sset, kind))


def _stings(sset):
    """Every transition sting available for a set."""
    d = _kind_dir(sset, "transition")
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.lower().endswith(AUDIO_EXT) and not f.startswith("."))


# --------------------------------------------------------------------------- #
#  Gemini text — writes the Arabic station ID from a short prompt
# --------------------------------------------------------------------------- #
def _gemini_text(prompt):
    api_key = _env("GEMINI_API_KEY").strip()
    if not api_key:
        return None
    model = (_env("GEMINI_TEXT_MODEL", "gemini-3.5-flash").strip()
             or "gemini-3.5-flash")
    base = _env("GEMINI_API_URL",
                "https://generativelanguage.googleapis.com/v1beta/models")
    full = (
        "أنت كاتب إعلانات إذاعية محترف لإذاعة \"بوزيد إف إم\" التونسية. "
        "اكتب مُعيّن محطة (jingle) قصيرًا جدًا من جملة إلى جملتين فقط "
        "بالعربية، جذّابًا وإيقاعيًّا وسهل القراءة الصوتية، يذكر اسم الإذاعة "
        "\"بوزيد إف إم\"، دون أي رموز أو علامات اقتباس أو شرح، "
        "بناءً على الفكرة التالية:\n" + prompt
    )
    payload = {
        "contents": [{"parts": [{"text": full}]}],
        "generationConfig": {"temperature": 0.9},
    }
    url = base.rstrip("/") + "/" + model + ":generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("x-goog-api-key", api_key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("Gemini text failed (%s)\n" % e)
        return None
    try:
        cands = data.get("candidates") or []
        parts = (cands[0].get("content", {}) or {}).get("parts", []) or []
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        text = ""
    text = re.sub(r"<think>.*?</think>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    for ch in ('"', "«", "»", "*", "#", "`"):
        text = text.replace(ch, "")
    return text.strip() or None


def _slugify(s):
    s = re.sub(r"[^\w\u0600-\u06FF]+", "_", s or "").strip("_")
    return s[:40] or "jingle"


# --------------------------------------------------------------------------- #
#  draft — write the script only, for review before generating
# --------------------------------------------------------------------------- #
def cmd_draft(prompt):
    text = _gemini_text(prompt)
    if not text:
        print("✗ Gemini did not return any text — check GEMINI_API_KEY/model")
        return 1
    try:
        with open(DRAFT_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:  # noqa: BLE001
        pass
    # Markers let the TUI pull the exact script out of the streamed log.
    print("<<<DRAFT")
    print(text)
    print("DRAFT>>>")
    return 0


# --------------------------------------------------------------------------- #
#  Generated bed / SFX (Lyria 3 + ElevenLabs)
# --------------------------------------------------------------------------- #
def _resolve_bed(sset, source, bed_prompt, tmp):
    """Return a music-bed file path for the voiced jingle.
    source: assets (mastered) | lyria | eleven. Falls back to mastered."""
    source = (source or "assets").lower()
    if source == "lyria":
        if lyria_music is None:
            print("  ⚠ Lyria module unavailable — using mastered bed")
        else:
            prompt = bed_prompt or (
                "warm, uplifting Tunisian radio station music bed, oud and "
                "light percussion, steady groove, no vocals")
            out = os.path.join(tmp, "bed_lyria.mp3")
            print("  🎵 generating bed with Lyria 3…")
            got = lyria_music.generate(prompt, out, instrumental=True)
            if got:
                return got
            print("  ⚠ Lyria bed failed — falling back to mastered bed")
    elif source == "eleven":
        if elevenlabs_audio is None:
            print("  ⚠ ElevenLabs module unavailable — using mastered bed")
        else:
            prompt = bed_prompt or (
                "warm, uplifting Tunisian radio station music bed, oud and "
                "light percussion, steady groove, instrumental")
            out = os.path.join(tmp, "bed_eleven.mp3")
            print("  🎵 generating bed with Eleven Music…")
            got = elevenlabs_audio.music(prompt, out, seconds=20,
                                         instrumental=True)
            if got:
                return got
            print("  ⚠ Eleven Music bed failed — falling back to mastered bed")
    return _asset(sset, "beds")


# --------------------------------------------------------------------------- #
#  Builders
# --------------------------------------------------------------------------- #
def _resolve_text(script, prompt):
    """Voiced scripts are NOT auto-written at build time. Use --script
    verbatim; only fall back to Gemini if --prompt was given explicitly."""
    if script and script.strip():
        return script.strip()
    if prompt and prompt.strip():
        print("ℹ no --script given; drafting from --prompt with Gemini…")
        return _gemini_text(prompt)
    return None


def build_voiced(script, prompt, voice, sset, name, bed_source, bed_prompt,
                 dry=False):
    text = _resolve_text(script, prompt)
    if not text:
        print("✗ no script. Draft one first (the 'draft' step) or pass "
              "--script / --prompt.")
        return 1
    print("📝 station ID:", text)
    tmp = tempfile.mkdtemp(prefix="jingle_")
    voice_wav = os.path.join(tmp, "voice.wav")
    try:
        tts_engine.synthesize(text, voice_wav, voice=voice or None)
    except Exception as e:  # noqa: BLE001
        print("✗ TTS failed:", e)
        return 1
    prefix = "dry_" if dry else "id_"
    base = _slugify(name) if name else (prefix + time.strftime("%Y%m%d_%H%M%S"))
    out = os.path.join(jingles_dir(), base + ".mp3")
    if dry:
        # Voice only — no bed, no sting: clean spoken ID, broadcast-leveled.
        print("  (voice only — no music bed)")
        audio_mix.build(voice_wav, out, bed=None, intro=None, outro=None)
        return out
    bed = _resolve_bed(sset, bed_source, bed_prompt, tmp)
    intro = _asset(sset, "transition") or _asset(sset, "intro")
    print("  bed:  ", os.path.relpath(bed, ROOT) if bed else "(none)")
    print("  sting:", os.path.relpath(intro, ROOT) if intro else "(none)")
    # Broadcast resting level (BED_GAIN env / default) so jingles sit at the
    # same loud, well-balanced bed as every other show.
    audio_mix.build(voice_wav, out, bed=bed, intro=intro, outro=None)
    return out


def build_sfx(sset, name, source, sfx_prompt):
    base = _slugify(name) if name else ("sfx_" + time.strftime("%Y%m%d_%H%M%S"))
    out = os.path.join(jingles_dir(), base + ".mp3")
    source = (source or "assets").lower()
    if source == "eleven":
        if elevenlabs_audio is None:
            print("✗ ElevenLabs module unavailable")
            return 1
        prompt = sfx_prompt or "clean modern radio station whoosh transition sting"
        tmp = tempfile.mkdtemp(prefix="sfx_")
        raw = os.path.join(tmp, "sfx.mp3")
        print("  🎛 generating SFX with ElevenLabs…")
        got = elevenlabs_audio.sfx(prompt, raw, duration=3, prompt_influence=0.5)
        if not got:
            print("✗ ElevenLabs SFX failed")
            return 1
        # loudness-normalize to broadcast level via the mixer's concat path.
        audio_mix.concat([got], out)
        return out
    # mastered montage: intro -> random sting -> outro
    intro = _asset(sset, "intro")
    outro = _asset(sset, "outro")
    stings = _stings(sset)
    sting = random.choice(stings) if stings else _asset(sset, "transition")
    parts = [p for p in (intro, sting, outro) if p]
    if not parts:
        print("✗ no mastered SFX found for set '%s' (assets/%s/)" % (sset, sset))
        return 1
    for p in parts:
        print("  +", os.path.relpath(p, ROOT))
    audio_mix.concat(parts, out)
    return out


def _publish(local, air):
    """Copy into assets/jingles, optionally cut it on air, always report."""
    aj = os.path.join(ROOT, "assets", "jingles")
    os.makedirs(aj, exist_ok=True)
    try:
        shutil.copy2(local, os.path.join(aj, os.path.basename(local)))
    except Exception:  # noqa: BLE001
        pass
    print("✓ jingle ready:", local)
    print("  • saved to your music library — it'll be picked up on the next "
          "rescan")
    if not air:
        return 0
    try:
        import azuracast
        ac = azuracast.AzuraCast()
        dest = "Jingles/" + os.path.basename(local)
        print("uploading ->", dest, "...")
        ac.upload(local, dest)
        res = ac.play_now(dest)
        if isinstance(res, dict) and res.get("on_air"):
            print("✓ now ON AIR")
        else:
            print("⚠ uploaded but playback not confirmed — the AzuraCast API "
                  "key needs admin rights")
    except Exception as e:  # noqa: BLE001
        print("✗ could not cut it on air (it may have uploaded):", e)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="Bouzid FM jingle studio")
    sub = ap.add_subparsers(dest="mode", required=True)

    d = sub.add_parser("draft", help="write the Arabic ID script only (review)")
    d.add_argument("--prompt", required=True)

    v = sub.add_parser("voiced", help="spoken script over a mastered/AI bed")
    v.add_argument("--script", default=None, help="exact words to speak")
    v.add_argument("--prompt", default=None, help="idea (Gemini drafts if no script)")
    v.add_argument("--voice", default=None)
    v.add_argument("--set", dest="sset", default="station")
    v.add_argument("--name", default=None)
    v.add_argument("--bed-source", dest="bed_source", default="assets",
                   choices=["assets", "lyria", "eleven"])
    v.add_argument("--bed-prompt", dest="bed_prompt", default=None)
    v.add_argument("--air", action="store_true")

    o = sub.add_parser("voiceonly", help="clean spoken script, no music bed")
    o.add_argument("--script", default=None)
    o.add_argument("--prompt", default=None)
    o.add_argument("--voice", default=None)
    o.add_argument("--name", default=None)
    o.add_argument("--air", action="store_true")

    s = sub.add_parser("sfx", help="sound-effect sting, no voice")
    s.add_argument("--set", dest="sset", default="station")
    s.add_argument("--name", default=None)
    s.add_argument("--source", default="assets", choices=["assets", "eleven"])
    s.add_argument("--sfx-prompt", dest="sfx_prompt", default=None)
    s.add_argument("--air", action="store_true")

    args = ap.parse_args()
    if args.mode == "draft":
        return cmd_draft(args.prompt)
    if args.mode == "voiced":
        out = build_voiced(args.script, args.prompt, args.voice, args.sset,
                           args.name, args.bed_source, args.bed_prompt,
                           dry=False)
    elif args.mode == "voiceonly":
        out = build_voiced(args.script, args.prompt, args.voice, "station",
                           args.name, "assets", None, dry=True)
    else:
        out = build_sfx(args.sset, args.name, args.source, args.sfx_prompt)
    if isinstance(out, int):
        return out
    return _publish(out, args.air)


if __name__ == "__main__":
    sys.exit(main() or 0)
