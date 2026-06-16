#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — broadcast voice processor ("radio imaging" chain).

Raw neural-TTS voices sound thin and flat — that "AI" giveaway. Real radio
station IDs and news anchors run the voice through a heavy processing chain:
pitch weight, warmth + presence EQ, de-essing, compression and a touch of
harmonic excitement, finished with a peak limiter. This module builds that
chain as an ffmpeg ``-af`` string and applies it to a voice file.

The goal: take ``ar-TN-HediNeural`` (or any TTS voice) and make it sound like
a big, dark, confident radio voice — NOT a robot.

*** PORTABLE: this version probes the local ffmpeg and only uses filters that
actually exist. If the build has `rubberband` it uses it for high-quality
pitch weight; otherwise it falls back to a universal `asetrate` pitch shift.
`aexciter` / `deesser` are dropped automatically when unavailable. So it runs
on a stock distro ffmpeg without crashing. ***

All knobs are tunable from .env so you can taste it by ear on the box:

    BOUZID_VOICE_PITCH      pitch scale, <1 = deeper. default 0.91 (~ -1.7 semis)
    BOUZID_VOICE_WARMTH     dB boost around 120 Hz (chest/body). default 3.0
    BOUZID_VOICE_PRESENCE   dB boost around 3-5 kHz (clarity).   default 2.5
    BOUZID_VOICE_EXCITE     harmonic exciter amount.             default 1.6
    BOUZID_VOICE_COMP       compressor ratio (density).          default 4.0
    BOUZID_VOICE_DEESS      de-esser intensity 0..1.             default 0.35
    BOUZID_VOICE_FX         master on/off (1/0).                 default 1

CLI:
    python3 scripts/voice_fx.py --in voice.wav --out voice_fx.wav
    python3 scripts/voice_fx.py --in v.wav --out v.wav --pitch 0.88 --print
    python3 scripts/voice_fx.py --probe        # show what ffmpeg supports
"""
import argparse
import os
import subprocess
import sys

_FILTER_CACHE = None


def _envf(name, default):
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return float(default)


def available_filters():
    """Return the set of ffmpeg audio-filter names this build supports."""
    global _FILTER_CACHE
    if _FILTER_CACHE is not None:
        return _FILTER_CACHE
    names = set()
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            toks = line.split()
            # rows look like: " T.. acompressor   A->A   desc"
            if len(toks) >= 3 and "->" in toks[2]:
                names.add(toks[1])
    except Exception:
        pass
    _FILTER_CACHE = names
    return names


def enabled():
    """Master switch. Set BOUZID_VOICE_FX=0 to bypass all processing."""
    return (os.environ.get("BOUZID_VOICE_FX", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def _pitch_parts(pitch, has):
    """Pitch-shift filter pieces. rubberband if available, else asetrate."""
    if abs(pitch - 1.0) <= 0.001:
        return []
    if "rubberband" in has:
        return ["rubberband=pitch=%.4f:pitchq=quality:channels=together"
                % pitch]
    # Universal fallback: reinterpret sample rate (shifts pitch + tempo),
    # then restore tempo with atempo, then normalise rate back to 44100.
    # atempo must stay within 0.5..2.0 — our pitch range (0.8..1.2) is safe.
    sr = int(round(44100 * pitch))
    tempo = 1.0 / pitch
    return ["asetrate=%d" % sr, "atempo=%.5f" % tempo, "aresample=44100"]


def build_filter(pitch=None, warmth=None, presence=None, excite=None,
                 comp=None, deess=None, has=None):
    """Return an ffmpeg audio-filter string for the broadcast voice chain.

    Order matters: pitch-shift first (so EQ acts on the final timbre), then
    clean-up + tone shaping, de-ess, compress for density, excite for life,
    limit for safety. NO loudnorm here — the mixer applies the final
    broadcast loudness so we don't double-normalise.
    """
    if has is None:
        has = available_filters()
    pitch = _envf("BOUZID_VOICE_PITCH", 0.91) if pitch is None else pitch
    warmth = _envf("BOUZID_VOICE_WARMTH", 3.0) if warmth is None else warmth
    presence = (_envf("BOUZID_VOICE_PRESENCE", 2.5)
                if presence is None else presence)
    excite = _envf("BOUZID_VOICE_EXCITE", 1.6) if excite is None else excite
    comp = _envf("BOUZID_VOICE_COMP", 4.0) if comp is None else comp
    deess = _envf("BOUZID_VOICE_DEESS", 0.35) if deess is None else deess

    parts = ["aresample=44100"]
    # 1) Pitch weight. <1 = deeper/bigger.
    parts += _pitch_parts(pitch, has)
    # 2) Clean rumble, then shape the tone: warmth, de-mud, presence, air.
    #    highpass / equalizer / treble are core filters (always present).
    parts += [
        "highpass=f=80",
        "equalizer=f=120:t=q:w=1.0:g=%.2f" % warmth,    # chest / body
        "equalizer=f=350:t=q:w=1.4:g=-2.5",             # remove boxy mud
        "equalizer=f=2800:t=q:w=1.6:g=%.2f" % presence, # presence
        "equalizer=f=5500:t=q:w=2.2:g=%.2f" % (presence * 0.9),  # clarity
        "treble=g=2:f=10000",                            # air / sheen
    ]
    # 3) De-ess (the presence boost can sharpen S sounds) — if supported.
    if deess > 0 and "deesser" in has:
        parts.append("deesser=i=%.2f" % min(max(deess, 0.0), 1.0))
    # 4) Compress for that dense, always-present radio level.
    if "acompressor" in has:
        parts.append(
            "acompressor=threshold=-19dB:ratio=%.2f:attack=8:release=140:"
            "makeup=3" % comp)
    # 5) Harmonic excitement — adds "alive"/analog sparkle — if supported.
    #    When aexciter is missing, fake a little air with a high shelf.
    if excite > 0:
        if "aexciter" in has:
            parts.append("aexciter=amount=%.2f:blend=3" % excite)
        else:
            parts.append("treble=g=%.2f:f=7500" % min(excite, 3.0))
    # 6) Catch peaks without auto-levelling (mixer owns final loudness).
    if "alimiter" in has:
        parts.append("alimiter=limit=0.95:level=false")
    else:
        parts.append("volume=0.97")
    return ",".join(parts)


def process(in_path, out_path, **kw):
    """Apply the broadcast chain to `in_path`, writing `out_path`.

    If BOUZID_VOICE_FX is disabled, just transcodes the input through
    unchanged so callers can always route through this function safely.
    """
    if not enabled():
        af = "aresample=44100"
    else:
        af = build_filter(**kw)
    codec = (["-codec:a", "libmp3lame", "-qscale:a", "2"]
             if out_path.lower().endswith(".mp3") else [])
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", in_path,
           "-af", af] + codec + [out_path]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        raise SystemExit("voice_fx ffmpeg failed")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Bouzid FM broadcast voice FX")
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--pitch", type=float, default=None)
    ap.add_argument("--warmth", type=float, default=None)
    ap.add_argument("--presence", type=float, default=None)
    ap.add_argument("--excite", type=float, default=None)
    ap.add_argument("--comp", type=float, default=None)
    ap.add_argument("--deess", type=float, default=None)
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the ffmpeg filter string and exit")
    ap.add_argument("--probe", action="store_true",
                    help="report which relevant filters this ffmpeg has")
    a = ap.parse_args()
    if a.probe:
        has = available_filters()
        for f in ("rubberband", "aexciter", "deesser", "acompressor",
                  "alimiter", "equalizer", "highpass", "treble",
                  "asetrate", "atempo"):
            print("%-12s %s" % (f, "yes" if f in has else "NO"))
        print("pitch method:",
              "rubberband" if "rubberband" in has else "asetrate fallback")
        return
    kw = dict(pitch=a.pitch, warmth=a.warmth, presence=a.presence,
              excite=a.excite, comp=a.comp, deess=a.deess)
    if a.show:
        print(build_filter(**kw))
        return
    if not a.inp or not a.out:
        ap.error("--in and --out are required (unless --print/--probe)")
    process(a.inp, a.out, **kw)
    print("Wrote", a.out)


if __name__ == "__main__":
    main()
