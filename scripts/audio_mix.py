#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — broadcast audio mixer (pure ffmpeg, no Python deps).

Builds a broadcast-ready show:

    [intro] -> [ voice over a ducked music bed ] -> [transition] -> ... -> [outro]

The music bed is looped to the voice length, brought up to a present resting
level, and side-chain compressed so it "ducks" under the voice and swells back
in the gaps. Segments are then leveled and concatenated (optionally cross-
faded) and the whole thing is normalized to a punchy broadcast loudness.

EVERYTHING IS TUNABLE via a MasterParams dict so the same code drives both the
initial generation and the "remaster" feature (re-mix an existing voice track
with new parameters — no re-synthesis needed). See DEFAULTS below.

Usage:
    python3 scripts/audio_mix.py --voice voice.wav --out show.mp3 \
        --bed assets/station/bed/station_bed.mp3 \
        --intro assets/station/intro/station_intro.mp3 \
        --outro assets/station/outro/station_outro.mp3

Any of --bed / --intro / --outro / --jingle may be omitted.
"""
import argparse
import os
import subprocess
import sys
import tempfile

SR = 44100

# --------------------------------------------------------------------------- #
#  Master parameters (the single source of truth for the mix balance).
#
#  bed_gain    resting music-bed level (0..1) when the voice is silent.
#  duck        how hard the bed ducks UNDER the voice (0..1). 0 = barely,
#              1 = slammed. Lower => the bed stays more present.
#  voice_gain  linear gain applied to the voice before mixing.
#  lufs        integrated loudness target for the final master (negative dB).
#  intro_gain  extra gain on the intro segment (1.0 = leveled like the body).
#  outro_gain  extra gain on the outro segment.
#  overlap     crossfade seconds between consecutive segments (0 = hard cut).
#  fade_in     bed fade-in seconds at the head.
#  fade_out    bed fade-out seconds at the tail.
#  lead        seconds the bed plays alone before the voice starts.
#  tail        seconds the bed plays alone after the voice ends.
#
#  Punchy broadcast defaults: a LOUD, present bed (the old 0.5 / ratio-18 mix
#  buried it), a moderate duck so you still hear the music under the voice, and
#  a -12 LUFS master.
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "bed_gain": 0.72,
    "duck": 0.55,
    "voice_gain": 1.0,
    "lufs": -12.0,
    "intro_gain": 1.0,
    "outro_gain": 1.0,
    "overlap": 0.0,
    "fade_in": 1.0,
    "fade_out": 2.0,
    "lead": 0.5,
    "tail": 2.0,
}

# Tunable keys exposed on the CLI / remaster UI.
PARAM_KEYS = list(DEFAULTS.keys())

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Default station imaging folders (uniform layout: assets/station/<kind>).
ASSET_DIRS = {
    "intro": os.path.join(ROOT, "assets", "station", "intro"),
    "outro": os.path.join(ROOT, "assets", "station", "outro"),
    "jingle": os.path.join(ROOT, "assets", "jingles"),
    "bed": os.path.join(ROOT, "assets", "station", "bed"),
}
_AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus")


# --------------------------------------------------------------------------- #
#  Parameters
# --------------------------------------------------------------------------- #
def _coerce_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def make_params(overrides=None):
    """Build a full MasterParams dict from DEFAULTS + env + explicit overrides.

    Env fallbacks (handy for quick tuning without code): BED_GAIN, DUCK,
    VOICE_GAIN, MASTER_LUFS.
    """
    p = dict(DEFAULTS)
    env_map = {"bed_gain": "BED_GAIN", "duck": "DUCK",
               "voice_gain": "VOICE_GAIN", "lufs": "MASTER_LUFS"}
    for key, env in env_map.items():
        ev = os.environ.get(env, "").strip()
        if ev:
            p[key] = _coerce_float(ev, p[key])
    if overrides:
        for k, v in overrides.items():
            if v is None:
                continue
            if k in p:
                p[k] = _coerce_float(v, p[k])
    # Clamp to sane ranges.
    p["bed_gain"] = max(0.05, min(p["bed_gain"], 1.5))
    p["duck"] = max(0.0, min(p["duck"], 1.0))
    p["voice_gain"] = max(0.1, min(p["voice_gain"], 4.0))
    p["lufs"] = max(-30.0, min(p["lufs"], -6.0))
    p["intro_gain"] = max(0.1, min(p["intro_gain"], 4.0))
    p["outro_gain"] = max(0.1, min(p["outro_gain"], 4.0))
    p["overlap"] = max(0.0, min(p["overlap"], 5.0))
    for k in ("fade_in", "fade_out", "lead", "tail"):
        p[k] = max(0.0, min(p[k], 10.0))
    return p


def _duck_to_sidechain(duck):
    """Map duck (0..1) to sidechaincompress ratio/threshold/release.

    duck=0   -> very gentle (ratio ~2, high threshold) : bed stays loud
    duck=1   -> hard (ratio ~16, low threshold)        : bed slammed away
    """
    ratio = 2.0 + duck * 14.0
    threshold = 0.10 - duck * 0.085          # 0.10 .. 0.015
    release = 280 + int(duck * 220)          # 280 .. 500 ms
    return ratio, max(threshold, 0.012), release


# --------------------------------------------------------------------------- #
#  Asset auto-pick
# --------------------------------------------------------------------------- #
def autopick(kind):
    """Return the station asset the app should use for `kind`.

    intro / outro / bed defer to the shared asset library (pinned default or
    newest variant). Jingles fall back to the newest file in the folder.
    """
    if kind in ("intro", "outro", "bed"):
        try:
            import assets_lib
            p = assets_lib.resolve("station", kind)
            if p:
                return p
        except Exception:  # noqa: BLE001
            pass
    d = ASSET_DIRS.get(kind)
    if not d or not os.path.isdir(d):
        return None
    files = [os.path.join(d, f) for f in os.listdir(d)
             if f.lower().endswith(_AUDIO_EXT) and not f.startswith(".")]
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


# --------------------------------------------------------------------------- #
#  ffmpeg helpers
# --------------------------------------------------------------------------- #
def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        raise SystemExit("ffmpeg failed: " + " ".join(cmd[:6]) + " ...")
    return p


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        stdout=subprocess.PIPE).stdout.decode().strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _norm(src, dst):
    """Re-encode any input to a consistent stereo / 44.1k WAV."""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
          "-ar", str(SR), "-ac", "2", dst])


# --------------------------------------------------------------------------- #
#  The voice-over-bed body
# --------------------------------------------------------------------------- #
def make_body(voice, bed, out, params=None, bed_gain=None):
    """Voice (loudness-normalized) over a present, side-chain-ducked bed.

    `params` is a MasterParams dict (see DEFAULTS). `bed_gain` is accepted for
    backwards compatibility and, if given, overrides params['bed_gain'].
    """
    p = make_params(params if isinstance(params, dict) else None)
    if bed_gain is not None:
        p["bed_gain"] = make_params({"bed_gain": bed_gain})["bed_gain"]

    if not bed or not os.path.exists(bed):
        # No bed: still bring the voice up to a consistent, audible level.
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", voice,
              "-af", "aresample=%d,aformat=channel_layouts=stereo,"
              "volume=%.3f,dynaudnorm=f=250:g=15:p=0.9"
              % (SR, p["voice_gain"]),
              "-ar", str(SR), "-ac", "2", out])
        return out

    gain = p["bed_gain"]
    lead, tail = p["lead"], p["tail"]
    fade_in, fade_out = p["fade_in"], p["fade_out"]
    ratio, thresh, release = _duck_to_sidechain(p["duck"])
    vdur = duration(voice)
    total = lead + vdur + tail
    fade_out_start = max(total - fade_out, 0.0)
    fc = (
        "[0:a]aresample=%d,aformat=channel_layouts=stereo,"
        "volume=%.3f,dynaudnorm=f=200:g=12:p=0.95,"
        "adelay=%d|%d,apad=pad_dur=%.3f[vc];"
        "[1:a]aresample=%d,aformat=channel_layouts=stereo,volume=%.3f,"
        "atrim=0:%.3f,afade=t=in:st=0:d=%.3f,afade=t=out:st=%.3f:d=%.3f[bed];"
        "[vc]asplit=2[v1][vsc];"
        "[bed][vsc]sidechaincompress=threshold=%.4f:ratio=%.2f:"
        "attack=12:release=%d:makeup=1[bedd];"
        "[v1][bedd]amix=inputs=2:duration=longest:normalize=0,"
        "alimiter=limit=0.97[mix]"
        % (SR, p["voice_gain"], int(lead * 1000), int(lead * 1000), tail,
           SR, gain, total, max(fade_in, 0.01), fade_out_start,
           max(fade_out, 0.01), thresh, ratio, release)
    )
    _run(["ffmpeg", "-y", "-loglevel", "error",
          "-i", voice, "-stream_loop", "-1", "-i", bed,
          "-filter_complex", fc, "-map", "[mix]", "-t", "%.3f" % total,
          "-ar", str(SR), "-ac", "2", out])
    return out


# --------------------------------------------------------------------------- #
#  Concatenation / final master
# --------------------------------------------------------------------------- #
def concat(parts, out, params=None, part_gains=None):
    """Level each part, (optionally) crossfade them, then master to target LUFS.

    parts        list of audio paths in order.
    part_gains   optional list of linear gains (same length as parts) applied
                 after per-part leveling — e.g. to lift the intro/outro.
    """
    parts = [p for p in parts if p]
    if not parts:
        raise SystemExit("nothing to concat")
    p = make_params(params if isinstance(params, dict) else None)
    if part_gains is None:
        part_gains = [1.0] * len(parts)
    part_gains = (list(part_gains) + [1.0] * len(parts))[:len(parts)]

    inputs = []
    for path in parts:
        inputs += ["-i", path]
    n = len(parts)
    overlap = p["overlap"]
    lufs = p["lufs"]

    # Per-part: stereo, leveled to target, then its individual gain.
    fc = "".join(
        "[%d:a]aformat=channel_layouts=stereo,aresample=%d,"
        "loudnorm=I=%.1f:TP=-1.5:LRA=11,volume=%.3f[a%d];"
        % (i, SR, lufs, part_gains[i], i)
        for i in range(n))

    if overlap > 0.01 and n > 1:
        prev = "a0"
        for i in range(1, n):
            label = "x%d" % i
            fc += "[%s][a%d]acrossfade=d=%.3f:c1=tri:c2=tri[%s];" % (
                prev, i, overlap, label)
            prev = label
        fc += "[%s]alimiter=limit=0.98[out]" % prev
    else:
        fc += "".join("[a%d]" % i for i in range(n))
        fc += "concat=n=%d:v=0:a=1,alimiter=limit=0.98[out]" % n

    _run(["ffmpeg", "-y", "-loglevel", "error"] + inputs +
         ["-filter_complex", fc, "-map", "[out]",
          "-codec:a", "libmp3lame", "-qscale:a", "2", out])
    return out


def build(voice, out, bed=None, intro=None, outro=None, jingle=None,
          params=None, bed_gain=None):
    """intro -> (jingle) -> voice/bed body -> outro, mastered."""
    p = make_params(params if isinstance(params, dict) else None)
    if bed_gain is not None:
        p["bed_gain"] = make_params({"bed_gain": bed_gain})["bed_gain"]
    tmp = tempfile.mkdtemp(prefix="bouzidmix_")
    body = os.path.join(tmp, "body.wav")
    make_body(voice, bed, body, params=p)
    parts, gains = [], []
    for label, src in (("intro", intro), ("jingle", jingle)):
        if src and os.path.exists(src):
            n = os.path.join(tmp, label + ".wav")
            _norm(src, n)
            parts.append(n)
            gains.append(p["intro_gain"] if label == "intro" else 1.0)
    parts.append(body)
    gains.append(1.0)
    if outro and os.path.exists(outro):
        n = os.path.join(tmp, "outro.wav")
        _norm(outro, n)
        parts.append(n)
        gains.append(p["outro_gain"])
    concat(parts, out, params=p, part_gains=gains)
    return out


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _add_param_args(ap):
    ap.add_argument("--bed-gain", type=float, default=None,
                    help="resting bed volume 0..1 (default %.2f)"
                         % DEFAULTS["bed_gain"])
    ap.add_argument("--duck", type=float, default=None,
                    help="how hard the bed ducks under the voice 0..1 "
                         "(default %.2f; lower = more present bed)"
                         % DEFAULTS["duck"])
    ap.add_argument("--voice-gain", type=float, default=None,
                    help="voice gain (default %.2f)" % DEFAULTS["voice_gain"])
    ap.add_argument("--lufs", type=float, default=None,
                    help="final loudness target (default %.0f)"
                         % DEFAULTS["lufs"])
    ap.add_argument("--intro-gain", type=float, default=None,
                    help="intro segment gain (default %.2f)"
                         % DEFAULTS["intro_gain"])
    ap.add_argument("--outro-gain", type=float, default=None,
                    help="outro segment gain (default %.2f)"
                         % DEFAULTS["outro_gain"])
    ap.add_argument("--overlap", type=float, default=None,
                    help="crossfade seconds between segments (default %.2f)"
                         % DEFAULTS["overlap"])
    ap.add_argument("--fade-in", type=float, default=None,
                    help="bed fade-in seconds (default %.2f)"
                         % DEFAULTS["fade_in"])
    ap.add_argument("--fade-out", type=float, default=None,
                    help="bed fade-out seconds (default %.2f)"
                         % DEFAULTS["fade_out"])


def params_from_args(args):
    """Collect only the explicitly-passed param overrides from argparse."""
    m = {
        "bed_gain": args.bed_gain, "duck": args.duck,
        "voice_gain": args.voice_gain, "lufs": args.lufs,
        "intro_gain": args.intro_gain, "outro_gain": args.outro_gain,
        "overlap": args.overlap, "fade_in": args.fade_in,
        "fade_out": args.fade_out,
    }
    return {k: v for k, v in m.items() if v is not None}


def main():
    ap = argparse.ArgumentParser(description="Mix a Bouzid FM show")
    ap.add_argument("--voice", required=True, help="the spoken audio (wav/mp3)")
    ap.add_argument("--out", required=True, help="output mp3")
    ap.add_argument("--bed", default=None, help="background music bed")
    ap.add_argument("--intro", default=None)
    ap.add_argument("--outro", default=None)
    ap.add_argument("--jingle", default=None)
    ap.add_argument("--no-auto", action="store_true",
                    help="do not auto-pick default assets from assets/")
    _add_param_args(ap)
    args = ap.parse_args()
    bed, intro, outro, jingle = args.bed, args.intro, args.outro, args.jingle
    if not args.no_auto:
        bed = bed or autopick("bed")
        intro = intro or autopick("intro")
        outro = outro or autopick("outro")
        jingle = jingle or autopick("jingle")
    for label, pth in (("intro", intro), ("jingle", jingle),
                       ("bed", bed), ("outro", outro)):
        if pth:
            print("  %-7s %s" % (label, os.path.relpath(pth, ROOT)))
    build(args.voice, args.out, bed=bed, intro=intro, outro=outro,
          jingle=jingle, params=params_from_args(args))
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
