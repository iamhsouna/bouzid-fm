#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — unified, preset-driven imaging generator (Google-only).

EVERY show is an imaging show. The roster is derived straight from the presets
(shows/<slug>.json) plus the shared "station" set — there is no separate,
hardcoded imaging list to drift out of sync with Studio. Each show gets the
full, uniform set of assets under its OWN folder:

    assets/<show>/intro/        spoken station ID (Gemini TTS over a bed)
    assets/<show>/outro/        spoken sign-off  (Gemini TTS over a bed)
    assets/<show>/bed/          music bed        (Lyria 3)
    assets/<show>/transition/   short whoosh/transition sting (Lyria 3)
    assets/<show>/sting/        several bumper stings (Lyria 3)

  * music beds + transition + stings -> Lyria 3   (scripts/lyria_music.py)
  * spoken intros / outros           -> Gemini TTS (scripts/tts_engine.py)
                                        mixed under a fresh bed (audio_mix.py)

Prompts/scripts: the well-known shows ship CURATED prompts; every other preset
gets sensible defaults synthesized from its name (override per-preset by adding
an optional "imaging" block to its shows/<slug>.json).

VARIANT MODEL: every run writes a NEW, timestamped file next to the existing
ones — it never overwrites. The app automatically uses the MOST RECENT variant
for each (show, kind) unless the user pins one as the default (assets_lib /
the Imaging tab). So "regenerate" just means "make a newer one".

  python3 scripts/make_imaging.py                       # everything, all shows
  python3 scripts/make_imaging.py --list                # show the plan, no calls
  python3 scripts/make_imaging.py --show station        # one show (repeatable)
  python3 scripts/make_imaging.py --show cook --show sport
  python3 scripts/make_imaging.py --kind sting          # only stings (any show)
  python3 scripts/make_imaging.py --kind sting --replace # regen stings clean
  python3 scripts/make_imaging.py --show sport --kind bed
  python3 scripts/make_imaging.py --show station --kind intro --make-default

Flags:
  --show / --set   limit to a show (repeatable); default = all shows
  --kind           limit to a kind: bed|intro|outro|transition|sting (repeatable)
  --list           print the plan and exit (changes nothing)
  --dry-run        print the steps but skip the Gemini calls
  --replace        back up & remove existing variants in scope first
  --make-default   pin each freshly generated variant as the default

  NOTE: Lyria 3 is NOT on the Gemini free tier. This needs a PAID
  GEMINI_API_KEY and outbound access to generativelanguage.googleapis.com.
  Run it on your own machine, not in a sandbox.

The `bouzid imaging` command is a thin wrapper around this script.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import assets_lib   # noqa: E402  (variant resolver + pinned defaults + roster)
import tts_engine   # noqa: E402  (Google Gemini TTS wrapper)
import audio_mix    # noqa: E402  (ffmpeg mixer: build / make_body / concat)

try:
    import lyria_music   # noqa: E402  (Google Lyria 3 music)
except Exception:        # noqa: BLE001
    lyria_music = None

KINDS = ["bed", "intro", "outro", "transition", "sting"]
KIND_ORDER = {"bed": 0, "intro": 1, "outro": 2, "transition": 3, "sting": 4}
STING_LUFS = audio_mix.DEFAULTS.get("lufs", -12.0)

WHOOSH = ("short, clean, modern radio station whoosh transition sting, "
          "punchy, no vocals, instrumental")

DEFAULT_STINGS = [
    "bright radio bumper sting, rising synth and a clean hit, energetic, "
    "instrumental",
    "warm oud flourish sting with a soft percussion hit, Tunisian flavor, "
    "instrumental",
    "modern whoosh into a deep boom transition sting, clean, instrumental",
]

# --------------------------------------------------------------------------- #
#  Curated prompts/scripts for the well-known shows. Anything not listed here
#  (or overridden by a preset "imaging" block) is synthesized from the name.
#  Keys: voice, bed, intro, outro, transition, transition_secs,
#        stings (list), sting_count, sting_secs.
# --------------------------------------------------------------------------- #
STATION = {
    "name": "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645",
    "voice": "Kore",
    "bed": "warm, confident Tunisian radio station music bed, oud and light "
           "percussion over a steady modern groove, uplifting, no vocals, "
           "instrumental broadcast imaging",
    "intro": "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645\u060c "
             "\u0635\u0648\u062a \u0633\u064a\u062f\u064a \u0628\u0648\u0632\u064a\u062f "
             "\u0648\u0627\u0644\u062c\u0647\u0629. \u0623\u062e\u0628\u0627\u0631\u060c "
             "\u0648\u062b\u0642\u0627\u0641\u0629\u060c \u0648\u0645\u0648\u0633\u064a\u0642\u0649\u060c "
             "\u0639\u0644\u0649 \u0645\u062f\u0627\u0631 \u0627\u0644\u064a\u0648\u0645.",
    "outro": "\u0643\u0627\u0646 \u0645\u0639\u0643\u0645 \u0628\u0648\u0632\u064a\u062f "
             "\u0625\u0641 \u0625\u0645. \u0634\u0643\u0631\u0627\u064b \u0639\u0644\u0649 "
             "\u0627\u0644\u0645\u062a\u0627\u0628\u0639\u0629\u060c \u0648\u0625\u0644\u0649 "
             "\u0627\u0644\u0644\u0642\u0627\u0621.",
    "transition": WHOOSH, "transition_secs": 2.8,
    "stings": [
        "bright radio bumper sting, rising synth and a clean hit, energetic, "
        "instrumental",
        "warm oud flourish sting with a soft percussion hit, Tunisian flavor, "
        "instrumental",
        "modern whoosh into a deep boom transition sting, clean, instrumental",
        "playful marimba and clap bumper sting, light and friendly, "
        "instrumental",
        "cinematic riser into a confident brass stab sting, instrumental",
    ],
    "sting_secs": 3.0,
}

CURATED = {
    "cook": {
        "voice": "Aoede",
        "bed": "light, warm cooking-show music bed, playful acoustic guitar "
               "and soft hand percussion, cheerful and homely, no vocals, "
               "instrumental",
        "intro": "\u0643\u0648\u0643 \u0645\u0639 \u0625\u064a\u0651\u0629\u060c "
                 "\u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645. "
                 "\u0627\u0644\u064a\u0648\u0645 \u0646\u0637\u064a\u0651\u0628\u0648 "
                 "\u0648\u0635\u0641\u0629 \u062a\u0648\u0646\u0633\u064a\u0629 "
                 "\u0628\u0646\u064a\u0646\u0629 \u0648\u0633\u0627\u0647\u0644\u0629.",
        "outro": "\u0628\u0627\u0644\u0635\u062d\u0629 \u0648\u0627\u0644\u0647\u0646\u0627. "
                 "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 \u0628\u0631\u0646\u0627\u0645\u062c "
                 "\u0643\u0648\u0643 \u0645\u0639 \u0625\u064a\u0651\u0629 \u0639\u0644\u0649 "
                 "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.",
        "transition": "short cheerful kitchen transition sting, light acoustic "
                      "flourish, instrumental", "transition_secs": 2.8,
    },
    "sidibouzid": {
        "voice": "Charon",
        "bed": "cinematic documentary music bed, warm strings and a subtle "
               "oud, reflective and dignified, slow build, no vocals, "
               "instrumental",
        "intro": "\u062d\u0643\u0627\u064a\u0629 \u0633\u064a\u062f\u064a "
                 "\u0628\u0648\u0632\u064a\u062f\u060c \u0639\u0644\u0649 "
                 "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645. "
                 "\u0642\u0635\u0629 \u0645\u0646 \u062a\u0627\u0631\u064a\u062e "
                 "\u0627\u0644\u0645\u062f\u064a\u0646\u0629 \u0648\u0627\u0644\u062c\u0647\u0629.",
        "outro": "\u0625\u0644\u0649 \u0647\u0646\u0627 \u062a\u0646\u062a\u0647\u064a "
                 "\u062d\u0643\u0627\u064a\u0629 \u0627\u0644\u064a\u0648\u0645\u060c "
                 "\u0645\u0646 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.",
        "transition": "epic cinematic documentary impact transition sting, "
                      "deep and dignified, instrumental", "transition_secs": 2.6,
    },
    "folktale": {
        "voice": "Sulafat",
        "bed": "mystical traditional storytelling music bed, soft flute and "
               "gentle hand percussion, dreamy and warm, no vocals, "
               "instrumental",
        "intro": "\u062d\u0643\u0627\u064a\u0629 \u0648\u062d\u0643\u0627\u064a\u0629\u060c "
                 "\u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.",
        "outro": "\u0648\u062a\u0648\u0641\u0651\u0649 \u0627\u0644\u062d\u0643\u0627\u064a\u0629. "
                 "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 \u0628\u0648\u0632\u064a\u062f "
                 "\u0625\u0641 \u0625\u0645.",
        "transition": "gentle magical chime transition sting, soft flute tail, "
                      "instrumental", "transition_secs": 2.6,
    },
    "heritagemusic": {
        "voice": "Gacrux",
        "bed": "traditional Tunisian heritage music bed, malouf-inspired "
               "strings and frame-drum percussion, elegant and timeless, no "
               "vocals, instrumental",
        "intro": "\u0645\u0648\u0633\u064a\u0642\u0649 \u0645\u0646 \u0627\u0644\u062a\u0631\u0627\u062b\u060c "
                 "\u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.",
        "outro": "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 \u0631\u0643\u0646 "
                 "\u0627\u0644\u062a\u0631\u0627\u062b \u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f "
                 "\u0625\u0641 \u0625\u0645.",
        "transition": "elegant malouf string flourish transition sting, "
                      "frame-drum hit, instrumental", "transition_secs": 2.6,
    },
    "sport": {
        "voice": "Puck",
        "bed": "energetic sports radio music bed, boom-bap drums and brass "
               "stabs, hype and driving, no vocals, instrumental",
        "intro": "\u0631\u064a\u0627\u0636\u0629 \u0628\u0648\u0632\u064a\u062f\u060c "
                 "\u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645. "
                 "\u0622\u062e\u0631 \u0623\u062e\u0628\u0627\u0631 \u0627\u0644\u0631\u064a\u0627\u0636\u0629.",
        "outro": "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 \u0631\u0643\u0646 "
                 "\u0627\u0644\u0631\u064a\u0627\u0636\u0629 \u0639\u0644\u0649 \u0628\u0648\u0632\u064a\u062f "
                 "\u0625\u0641 \u0625\u0645.",
        "transition": "punchy sports hit transition sting, brass stab and drum, "
                      "energetic, instrumental", "transition_secs": 2.4,
    },
}


# --------------------------------------------------------------------------- #
#  Build the imaging plan from the presets (+ the shared station set)
# --------------------------------------------------------------------------- #
def _load_preset(slug):
    p = os.path.join(ROOT, "shows", slug + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _default_bed_prompt(name):
    return ("warm, modern Tunisian radio music bed for the show \u00ab%s\u00bb, "
            "tasteful instrumentation, uplifting, no vocals, instrumental "
            "broadcast imaging" % name)


def _default_intro(name):
    return "%s\u060c \u0639\u0644\u0649 \u0625\u0630\u0627\u0639\u0629 " \
           "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645." % name


def _default_outro(name):
    return "\u0643\u0627\u0646 \u0645\u0639\u0627\u0643\u0645 %s \u0639\u0644\u0649 " \
           "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645." % name


def _set_path(slug, kind):
    """Canonical stem template: assets/<slug>/<kind>/<slug>_<kind>.mp3"""
    return "assets/%s/%s/%s_%s.mp3" % (slug, kind, slug, kind)


def build_set(slug):
    """Assemble the full asset list for one show (station or a preset)."""
    if slug == "station":
        c = dict(STATION)
        name = c.get("name", "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645")
        voice = c.get("voice", "Kore")
    else:
        preset = _load_preset(slug)
        name = preset.get("name") or slug
        c = dict(CURATED.get(slug, {}))
        c.update({k: v for k, v in (preset.get("imaging") or {}).items()
                  if v is not None})
        voice = c.get("voice") or preset.get("voice") or "Kore"

    bed_prompt = c.get("bed") or _default_bed_prompt(name)
    intro_script = c.get("intro") or _default_intro(name)
    outro_script = c.get("outro") or _default_outro(name)
    trans_prompt = c.get("transition") or WHOOSH
    trans_secs = float(c.get("transition_secs", 2.6))
    stings = c.get("stings") or DEFAULT_STINGS
    sting_count = int(c.get("sting_count", len(stings)))
    sting_secs = float(c.get("sting_secs", 3.0))

    assets = [
        {"kind": "bed", "path": _set_path(slug, "bed"), "music": bed_prompt},
        {"kind": "intro", "path": _set_path(slug, "intro"),
         "script": intro_script},
        {"kind": "outro", "path": _set_path(slug, "outro"),
         "script": outro_script},
        {"kind": "transition", "path": _set_path(slug, "transition"),
         "music": trans_prompt, "secs": trans_secs},
    ]
    for i in range(max(sting_count, 0)):
        assets.append({"kind": "sting", "path": _set_path(slug, "sting"),
                       "music": stings[i % len(stings)], "secs": sting_secs})
    return {"voice": voice, "assets": assets}


def _all_slugs():
    return ["station"] + assets_lib.preset_slugs()


def build_sets():
    return {slug: build_set(slug) for slug in _all_slugs()}


# Computed once at import: station + every preset = the full imaging roster.
SETS = build_sets()


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _abs(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def _rel(p):
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


def _variant_path(asset, stamp, idx=0):
    """Build a fresh, timestamped variant path from an asset's stem template.
    e.g. assets/sport/bed/sport_bed.mp3 -> assets/sport/bed/sport_bed_<stamp>.mp3
    """
    out_dir, base = os.path.split(_abs(asset["path"]))
    stem, ext = os.path.splitext(base)
    suffix = stamp if idx == 0 else "%s-%d" % (stamp, idx)
    return os.path.join(out_dir, "%s_%s%s" % (stem, suffix, ext))


def _have_key():
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return True
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("GEMINI_API_KEY="):
                    v = line.partition("=")[2].strip().strip('"').strip("'")
                    if v:
                        os.environ.setdefault("GEMINI_API_KEY", v)
                        return True
    except FileNotFoundError:
        pass
    return False


def _short_instrumental(src, dst, secs):
    """Trim a long Lyria clip to a short, broadcast-leveled sting with fades."""
    secs = float(secs)
    fade_out = max(secs - 0.4, 0.1)
    af = ("afade=t=in:st=0:d=0.12,"
          "afade=t=out:st=%.3f:d=0.4,"
          "loudnorm=I=%.1f:TP=-1.5:LRA=11" % (fade_out, STING_LUFS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-t", "%.3f" % secs, "-af", af,
           "-ar", "44100", "-ac", "2",
           "-codec:a", "libmp3lame", "-qscale:a", "2", dst]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        return None
    return dst


def _replace_scope(slug, kinds):
    """Back up & remove the EXISTING variants for (slug, each kind) so only the
    freshly generated one will remain. Used by --replace."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "assets", "_backup_%s" % stamp)
    moved = 0
    for kind in kinds:
        for v in assets_lib.variants(slug, kind):
            src = v["path"]
            dst = os.path.join(backup_dir, _rel(src))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.move(src, dst)
                moved += 1
            except OSError as e:
                sys.stderr.write("  could not back up %s: %s\n" % (src, e))
        assets_lib.clear_default(slug, kind)
    if moved:
        print("  backed up & removed %d existing variant(s) -> %s"
              % (moved, _rel(backup_dir)))
    return moved


# --------------------------------------------------------------------------- #
#  Generators (each returns the new variant path or None)
# --------------------------------------------------------------------------- #
def _gen_bed(asset, voice, tmp, stamp):
    out = _variant_path(asset, stamp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  bed   %s" % _rel(out))
    got = lyria_music.generate(asset["music"], out, instrumental=True)
    return out if got else None


def _gen_sting(asset, voice, tmp, stamp, idx=0):
    out = _variant_path(asset, stamp, idx)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  %-5s %s" % (asset["kind"], _rel(out)))
    raw = os.path.join(tmp, "raw_%s" % os.path.basename(out))
    got = lyria_music.generate(asset["music"], raw, instrumental=True)
    if not got:
        return None
    return out if _short_instrumental(raw, out, asset.get("secs", 2.8)) else None


def _latest_bed(slug):
    """Newest bed variant for a show (to mix voiced IDs over), or None."""
    vs = assets_lib.variants(slug, "bed")
    return vs[0]["path"] if vs else None


def _gen_voiced(asset, voice, tmp, slug, stamp):
    """Speak the Arabic script and mix it under this show's freshest bed."""
    out = _variant_path(asset, stamp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print("  %-5s %s  (voice: %s)" % (asset["kind"], _rel(out), voice))
    voice_wav = os.path.join(tmp, "voice_%s.wav" % os.path.basename(out))
    try:
        tts_engine.synthesize(asset["script"], voice_wav, voice=voice)
    except SystemExit as e:
        sys.stderr.write("  TTS failed: %s\n" % e)
        return None
    bed = _latest_bed(slug)
    audio_mix.build(voice_wav, out, bed=bed, intro=None, outro=None)
    return out


def regenerate(set_slugs, kinds, voice_override, dry_run, make_default):
    ok, fail = 0, 0
    for slug in set_slugs:
        cfg = SETS[slug]
        voice = voice_override or cfg.get("voice") or "Kore"
        assets = [a for a in cfg["assets"] if a["kind"] in kinds]
        if not assets:
            continue
        print("\n== %s ==" % slug)
        tmp = tempfile.mkdtemp(prefix="imaging_%s_" % slug)
        # bed first (voiced intros/outros mix over the newest bed), then rest.
        assets = sorted(assets, key=lambda a: KIND_ORDER.get(a["kind"], 9))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        sting_idx = 0
        for a in assets:
            if dry_run:
                print("  would generate %-5s -> %s"
                      % (a["kind"], _rel(_variant_path(a, stamp, sting_idx))))
                if a["kind"] == "sting":
                    sting_idx += 1
                continue
            try:
                if a["kind"] == "bed":
                    newp = _gen_bed(a, voice, tmp, stamp)
                elif a["kind"] == "transition":
                    newp = _gen_sting(a, voice, tmp, stamp)
                elif a["kind"] == "sting":
                    newp = _gen_sting(a, voice, tmp, stamp, sting_idx)
                    sting_idx += 1
                else:  # intro / outro
                    newp = _gen_voiced(a, voice, tmp, slug, stamp)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("  error on %s: %s\n" % (a["path"], e))
                newp = None
            if newp:
                ok += 1
                if make_default:
                    try:
                        assets_lib.set_default(slug, a["kind"],
                                               os.path.basename(newp))
                        print("      \u2605 pinned as default %s/%s"
                              % (slug, a["kind"]))
                    except Exception as e:  # noqa: BLE001
                        sys.stderr.write("  could not pin default: %s\n" % e)
            else:
                fail += 1
                print("  \u2717 failed: %s" % a["path"])
    return ok, fail


def print_plan(set_slugs, kinds):
    total = 0
    for slug in set_slugs:
        assets = [a for a in SETS[slug]["assets"] if a["kind"] in kinds]
        if not assets:
            continue
        print("\n== %s ==  (voice: %s)" % (slug, SETS[slug].get("voice")))
        for a in assets:
            total += 1
            how = "Lyria 3" if a["kind"] in ("bed", "transition", "sting") \
                else "Gemini TTS + bed"
            existing = len(assets_lib.variants(slug, a["kind"]))
            print("  %-11s %-46s [%s]  (%d existing)"
                  % (a["kind"], a["path"], how, existing))
    print("\n%d new variant(s) would be generated across %d show(s)."
          % (total, len(set_slugs)))


def main():
    ap = argparse.ArgumentParser(
        description="Generate Bouzid FM imaging variants with Google")
    ap.add_argument("--show", "--set", dest="sets", action="append",
                    choices=sorted(SETS.keys()),
                    help="limit to this show (repeatable); default: all")
    ap.add_argument("--kind", dest="kinds", action="append",
                    choices=KINDS,
                    help="limit to this kind (repeatable); default: all")
    ap.add_argument("--list", action="store_true",
                    help="print the plan and exit (changes nothing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the steps, but skip the Gemini calls")
    ap.add_argument("--voice", default=None,
                    help="override the Gemini TTS voice for every set")
    ap.add_argument("--replace", action="store_true",
                    help="back up & remove existing variants in scope first")
    ap.add_argument("--make-default", action="store_true",
                    help="pin each freshly generated variant as the default")
    args = ap.parse_args()

    slugs = args.sets or list(SETS.keys())
    kinds = args.kinds or list(KINDS)

    if args.list:
        print_plan(slugs, kinds)
        return 0

    print("Bouzid FM imaging generator (Google: Lyria 3 + Gemini TTS)")
    print("Variant model: a new timestamped file per run; the app uses the "
          "newest unless you pin a default.")
    print_plan(slugs, kinds)

    if lyria_music is None and any(
            a["kind"] in ("bed", "transition", "sting")
            for s in slugs for a in SETS[s]["assets"] if a["kind"] in kinds):
        print("\n\u2717 lyria_music module unavailable \u2014 cannot generate "
              "beds/stings.")
        return 1
    if not _have_key():
        print("\n\u2717 GEMINI_API_KEY is not set. Add it to .env (a PAID key "
              "is required \u2014 Lyria 3 is not on the free tier).")
        return 1

    if args.replace and not args.dry_run:
        print("\nReplacing: backing up the existing variants in scope \u2026")
        for slug in slugs:
            _replace_scope(slug, kinds)

    if args.dry_run:
        print("\n(dry run: no audio generated, nothing changed)")
        regenerate(slugs, kinds, args.voice, True, False)
        return 0

    ok, fail = regenerate(slugs, kinds, args.voice, False, args.make_default)

    print("\n\u2713 done: %d new variant(s) generated, %d failed." % (ok, fail))
    if fail:
        print("  Some assets failed. Existing variants are untouched (and any "
              "--replace backups are in assets/_backup_*).")
        return 1
    print("  The app now uses the freshest variant for each (show, kind) "
          "automatically \u2014 or pin a favourite in the Imaging tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
