#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — generic, preset-driven show engine (Google-only).

Every talk show on Bouzid FM is just a PRESET file in shows/<slug>.json. This
one engine reads a preset, writes today's episode with Google Gemini (with a
graceful built-in fallback), voices it with Google Gemini TTS, mixes it with a
mastered intro / music bed / transition / outro from assets/, and can push it
straight to AzuraCast — all configured from JSON.

    python3 scripts/show_engine.py --list
    python3 scripts/show_engine.py --show didyouknow --script-only
    python3 scripts/show_engine.py --show farmer --play
    python3 scripts/show_engine.py --show proverb --upload

Add a new show = drop a new JSON in shows/ (see shows/_SCHEMA.md). No code
changes needed. The TUI and `bin/bouzid show` pick it up automatically.

Env (per show, all optional): <SLUG>_VOICE (a Gemini voice),
BOUZID_<SLUG>_VOICE_FX. Voice + text both use Google.
"""
import argparse
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import assets_lib     # noqa: E402  (unified imaging resolver: latest or pinned)
import audio_mix      # noqa: E402
import make_bulletin  # noqa: E402  (reuse the Gemini text writer + news feeds)
import tts_engine     # noqa: E402
import voice_fx       # noqa: E402
try:
    import feeds       # noqa: E402  (optional live data)
except Exception:
    feeds = None

SHOWS_DIR = os.path.join(ROOT, "shows")

# Default Gemini voice for shows that don't pin their own (Kore = station voice).
DEFAULT_VOICE = "Kore"

ASSET_DIRMAP = {"intro": "intro", "outro": "outro",
                "bed": "beds", "transition": "transition"}
_AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus")


# --------------------------------------------------------------------------- #
# Preset loading
# --------------------------------------------------------------------------- #
def preset_path(slug):
    return os.path.join(SHOWS_DIR, slug + ".json")


def list_shows():
    if not os.path.isdir(SHOWS_DIR):
        return []
    return sorted(fn[:-5] for fn in os.listdir(SHOWS_DIR)
                  if fn.endswith(".json") and not fn.startswith("_"))


def load_preset(slug):
    p = preset_path(slug)
    if not os.path.exists(p):
        raise SystemExit("Unknown show '%s'. Available: %s"
                         % (slug, ", ".join(list_shows()) or "(none)"))
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("slug", slug)
    d.setdefault("name", slug)
    d.setdefault("title_en", slug)
    d.setdefault("emoji", "\U0001F399")
    d.setdefault("desc", "")
    d.setdefault("bed_gain", None)
    d.setdefault("markers", [])
    d.setdefault("angles", [])
    d.setdefault("inspiration", [])
    d.setdefault("inspiration_n", 3)
    d.setdefault("feed", "none")
    d.setdefault("fallback", [])
    d.setdefault("mix", {})
    d.setdefault("upload_folder", slug[:1].upper() + slug[1:])
    d.setdefault("music_subdir", slug)
    up = slug.upper()
    d.setdefault("voice_env", up + "_VOICE")
    d.setdefault("fx_env", "BOUZID_%s_VOICE_FX" % up)
    return d


# --------------------------------------------------------------------------- #
# Markers / prompt
# --------------------------------------------------------------------------- #
def _marker_ids(preset):
    return [m["id"] for m in preset["markers"] if m.get("id")]


def marker_re(preset):
    ids = _marker_ids(preset)
    if not ids:
        return None
    alt = "|".join(re.escape(i) for i in ids)
    return re.compile(r"\[\[\s*(?:%s)\s*\]\]" % alt)


def _feed_text(preset):
    kind = preset.get("feed", "none")
    if kind in ("", "none", None) or feeds is None:
        return ""
    try:
        return feeds.get(kind) or ""
    except Exception as e:
        sys.stderr.write("feed '%s' unavailable: %s\n" % (kind, e))
        return ""


def build_prompt(preset):
    now = datetime.datetime.now()
    random.seed(now.strftime("%Y-%m-%d") + ":" + preset["slug"])
    today = now.strftime("%Y-%m-%d")
    prompt = (preset.get("prompt") or "").replace("{date}", today)

    feed_text = _feed_text(preset)
    if feed_text:
        prompt += ("\n\n\u0645\u0639\u0637\u064a\u0627\u062a \u062d\u064a\u0651\u0629 "
                   "\u0644\u0627\u0632\u0645 \u062a\u0639\u062a\u0645\u062f \u0639\u0644\u064a\u0647\u0627 "
                   "(\u0648\u0645\u0627 \u062a\u062e\u062a\u0631\u0639\u0634 \u063a\u064a\u0631\u0647\u0627): "
                   + feed_text)

    angles = preset.get("angles") or []
    if angles:
        prompt += "\n\n" + angles[now.weekday() % len(angles)]

    insp = preset.get("inspiration") or []
    if insp:
        n = min(int(preset.get("inspiration_n", 3)), len(insp))
        prompt += ("\n\n\u0627\u0633\u062a\u0644\u0647\u0645 \u0645\u0646 \u0647\u0630\u0647 "
                   "\u0627\u0644\u0623\u0641\u0643\u0627\u0631 (\u0648\u0646\u0648\u0651\u0639 "
                   "\u0643\u0644 \u0645\u0631\u0629): "
                   + "\u060c ".join(random.sample(insp, n)))

    markers = preset.get("markers") or []
    if markers:
        spec = ("\n\n\u0627\u0633\u062a\u0639\u0645\u0644 \u0628\u0627\u0644\u0636\u0628\u0637 "
                "\u0647\u0630\u0627 \u0627\u0644\u062a\u0646\u0633\u064a\u0642 "
                "\u0628\u0627\u0644\u0639\u0644\u0627\u0645\u0627\u062a "
                "(\u0644\u0627 \u062a\u0643\u062a\u0628 \u0623\u064a \u0634\u064a\u0621 "
                "\u0642\u0628\u0644 \u0623\u0648\u0644 \u0639\u0644\u0627\u0645\u0629):\n\n")
        for m in markers:
            hint = m.get("hint") or m.get("display") or m["id"]
            spec += "[[%s]]\n... (%s) ...\n" % (m["id"], hint)
        prompt += spec
    return prompt


# --------------------------------------------------------------------------- #
# LLM (Google Gemini only, with built-in fallback)
# --------------------------------------------------------------------------- #
def write_show(prompt):
    out = make_bulletin._write_via_gemini(prompt)
    if out:
        return out
    sys.stderr.write("Gemini unreachable; using the built-in fallback episode.\n")
    return None


def fallback_show(preset):
    fb = preset.get("fallback") or []
    if not fb:
        return ("\u0623\u0647\u0644\u0627 \u0628\u064a\u0643\u0645 \u0641\u064a "
                + preset.get("name", "") + " \u0639\u0644\u0649 \u0625\u0630\u0627\u0639\u0629 "
                "\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.")
    parts = []
    for seg in fb:
        sid = seg.get("id")
        txt = seg.get("text", "")
        if sid:
            parts.append("[[%s]]\n%s" % (sid, txt))
        else:
            parts.append(txt)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Text shaping
# --------------------------------------------------------------------------- #
def clean(text, mre):
    if mre:
        text = mre.sub(" ", text)
    text = re.sub(r"[*#`>_]+", "", text)
    text = re.sub(r"^\s*[-\u2022]\s*", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_segments(full, mre):
    if not mre:
        return [clean(full, mre)]
    markers = list(mre.finditer(full))
    if not markers:
        return [clean(full, mre)]
    segs = []
    for i, m in enumerate(markers):
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(full)
        chunk = clean(full[start:end], mre)
        if chunk:
            segs.append(chunk)
    return segs or [clean(full, mre)]


def display(full, preset, mre):
    disp = full
    for m in preset.get("markers", []):
        token = "[[%s]]" % m["id"]
        label = m.get("display") or ("%s %s" % (preset.get("emoji", ""), m["id"]))
        disp = disp.replace(token, "\n%s\n" % label.strip())
    disp = re.sub(r"[*#`>_]+", "", disp)
    return disp.strip()


# --------------------------------------------------------------------------- #
# Voice / FX / assets
# --------------------------------------------------------------------------- #
def engine_voice(preset):
    """Pick the Gemini voice for this show: <SLUG>_VOICE, else the preset's
    own default, else the station default (Kore)."""
    voice = (os.environ.get(preset["voice_env"], "").strip()
             or (preset.get("voice") or "").strip()
             or DEFAULT_VOICE)
    return "google", voice


def fx_on(preset):
    if not voice_fx.enabled():
        return False
    return os.environ.get(preset["fx_env"], "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _autopick_dir(d):
    if not d or not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d)
                   if f.lower().endswith(_AUDIO_EXT) and not f.startswith("."))
    return os.path.join(d, files[0]) if files else None


def asset(preset, kind):
    """Resolve one imaging asset for this show via the shared asset library.

    The resolver always returns the variant the user pinned as default for
    (show, kind), or — if none is pinned — the most recently generated one.
    A show with its own assets_subdir uses its folder; intro/outro/bed fall
    back to the station's imaging when the show has none of its own.
    """
    show = ((preset.get("assets_subdir") or "").strip()
            or (preset.get("slug") or "").strip() or "station")
    p = assets_lib.resolve(show, kind)
    if p:
        return p
    if show != "station":
        return assets_lib.resolve("station", kind)
    return None


def build_show(preset, segments, out, params=None):
    """Synthesize each segment, persist the voice stems next to the show, write
    a <out>.master.json describing the mix, then assemble the mastered show.

    The persisted stems + manifest are what make `remaster` possible later
    WITHOUT re-synthesizing (no extra TTS cost).
    """
    backend, voice = engine_voice(preset)
    mix = preset.get("mix", {})
    bed = asset(preset, "bed") if mix.get("bed", True) else None
    intro = asset(preset, "intro") if mix.get("intro", True) else None
    outro = asset(preset, "outro") if mix.get("outro", True) else None
    trans = asset(preset, "transition") if mix.get("transition", True) else None
    use_fx = fx_on(preset)

    p = audio_mix.make_params(params if isinstance(params, dict) else None)
    if preset.get("bed_gain") is not None and not (params or {}).get("bed_gain"):
        p["bed_gain"] = audio_mix.make_params(
            {"bed_gain": preset.get("bed_gain")})["bed_gain"]

    master_dir = out + ".master"
    shutil.rmtree(master_dir, ignore_errors=True)
    os.makedirs(master_dir, exist_ok=True)

    voice_files = []
    for i, seg in enumerate(segments):
        if not seg:
            continue
        raw = os.path.join(master_dir, "voice_%d.wav" % len(voice_files))
        print("Synthesizing segment %d/%d (google / %s) \u2026"
              % (i + 1, len(segments), voice or "default"))
        tts_engine.synthesize(seg, raw, voice=voice or None)
        if use_fx:
            tmpfx = raw + ".fx.wav"
            voice_fx.process(raw, tmpfx)
            os.replace(tmpfx, raw)
        voice_files.append(raw)

    master = {
        "slug": preset.get("slug"),
        "voice": voice,
        "bed": bed, "intro": intro, "outro": outro, "transition": trans,
        "segments": [os.path.basename(f) for f in voice_files],
        "params": p,
    }
    with open(out + ".master.json", "w", encoding="utf-8") as fh:
        json.dump(master, fh, ensure_ascii=False, indent=2)

    _assemble(master_dir, master, out, p)
    return out


def _assemble(master_dir, master, out, params):
    """Build intro -> bodies (voice over ducked bed) + transitions -> outro
    from already-synthesized voice stems, then master to the target loudness."""
    p = audio_mix.make_params(params if isinstance(params, dict) else None)
    bed = master.get("bed")
    intro = master.get("intro")
    outro = master.get("outro")
    trans = master.get("transition")
    seg_names = master.get("segments") or []
    tmp = tempfile.mkdtemp(prefix="bouzidasm_")
    parts, gains = [], []
    if intro and os.path.exists(intro):
        parts.append(intro)
        gains.append(p["intro_gain"])
    n = len(seg_names)
    for i, name in enumerate(seg_names):
        vf = os.path.join(master_dir, name)
        if not os.path.exists(vf):
            continue
        body = os.path.join(tmp, "body_%d.wav" % i)
        audio_mix.make_body(vf, bed, body, params=p)
        parts.append(body)
        gains.append(1.0)
        if trans and os.path.exists(trans) and i < n - 1:
            parts.append(trans)
            gains.append(1.0)
    if outro and os.path.exists(outro):
        parts.append(outro)
        gains.append(p["outro_gain"])
    audio_mix.concat(parts, out, params=p, part_gains=gains)
    return out


def remaster(src, overrides=None, out=None):
    """Re-mix a previously generated show with new mastering parameters, reusing
    its saved voice stems (no re-synthesis). `src` is the show's .mp3 path."""
    mj = src + ".master.json"
    if not os.path.exists(mj) and src.endswith(".master.json"):
        mj, src = src, src[:-len(".master.json")]
    if not os.path.exists(mj):
        raise SystemExit(
            "No saved stems for %s (looked for %s). Only shows generated by "
            "this engine can be remastered." % (src, mj))
    with open(mj, encoding="utf-8") as fh:
        master = json.load(fh)
    master_dir = src + ".master"
    if not os.path.isdir(master_dir):
        raise SystemExit("Voice stems missing: %s" % master_dir)

    p = audio_mix.make_params(master.get("params") or {})
    p = audio_mix.make_params({**p, **(overrides or {})})

    if not out:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        root, ext = os.path.splitext(src)
        out = "%s__remaster_%s%s" % (root, stamp, ext or ".mp3")

    print("Remastering from saved voice stems (no re-synthesis) \u2026")
    for k in audio_mix.PARAM_KEYS:
        print("  %-11s %s" % (k, p[k]))
    _assemble(master_dir, master, out, p)

    nm = dict(master)
    nm["params"] = p
    with open(out + ".master.json", "w", encoding="utf-8") as fh:
        json.dump(nm, fh, ensure_ascii=False, indent=2)
    nd = out + ".master"
    if os.path.abspath(nd) != os.path.abspath(master_dir):
        shutil.rmtree(nd, ignore_errors=True)
        shutil.copytree(master_dir, nd)
    return out


def music_dir(preset):
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    d = os.path.join(md, preset.get("music_subdir", preset["slug"]))
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_list():
    shows = list_shows()
    if not shows:
        print("No shows defined. Run: python3 scripts/seed_presets.py")
        return
    print("Available shows (shows/<slug>.json):\n")
    for slug in shows:
        try:
            p = load_preset(slug)
            print("  %-14s %s %s" % (slug, p.get("emoji", ""),
                                     p.get("title_en") or slug))
            if p.get("desc"):
                print("  %-14s    %s" % ("", p["desc"]))
        except Exception as e:
            print("  %-14s (invalid: %s)" % (slug, e))


def deliver(out, upload_folder, args):
    """Optionally upload / cut-to-air / play a finished file."""
    if args.upload or args.air:
        import azuracast
        ac = azuracast.AzuraCast()
        dest = upload_folder.rstrip("/") + "/" + os.path.basename(out)
        print("Uploading to AzuraCast \u2026")
        print(json.dumps(ac.upload(out, dest), ensure_ascii=False)[:300])
        if args.air:
            try:
                res = ac.play_now(dest)
                print(json.dumps(res, ensure_ascii=False)[:300])
                if res.get("on_air"):
                    print("  \u2713 now ON AIR (cut in via interrupting queue)")
                else:
                    print("  \u26a0 uploaded, but the backend did not confirm "
                          "playback \u2014 check the AzuraCast admin API key.")
            except Exception as e:  # noqa: BLE001
                print("  \u2717 could not cut it on air: %s" % e)
                print("    (the file uploaded OK; press Skip or air it from "
                      "AzuraCast.)")
    if args.play:
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                        out])


def main():
    ap = argparse.ArgumentParser(
        description="Generate any Bouzid FM preset show (shows/<slug>.json)")
    ap.add_argument("--show", help="preset slug, e.g. farmer / proverb")
    ap.add_argument("--list", action="store_true", help="list available shows")
    ap.add_argument("--out", default=None)
    ap.add_argument("--text", help="skip the LLM and speak this text")
    ap.add_argument("--script-only", action="store_true",
                    help="print the episode script and exit")
    ap.add_argument("--no-mix", action="store_true",
                    help="raw voice only (no intro / bed / outro / transitions)")
    ap.add_argument("--remaster", metavar="SHOW.mp3",
                    help="re-mix an already-generated show with new mastering "
                         "params (uses saved voice stems, no re-synthesis)")
    ap.add_argument("--upload", action="store_true", help="push to AzuraCast")
    ap.add_argument("--air", action="store_true",
                    help="upload then cut it onto the air immediately")
    ap.add_argument("--play", action="store_true", help="play the result (ffplay)")
    audio_mix._add_param_args(ap)
    args = ap.parse_args()
    overrides = audio_mix.params_from_args(args)

    if args.remaster:
        out = remaster(args.remaster, overrides=overrides, out=args.out)
        print("Wrote", out)
        folder = "Shows"
        try:
            mj = json.load(open(out + ".master.json", encoding="utf-8"))
            slug = mj.get("slug")
            if slug:
                folder = load_preset(slug).get("upload_folder", folder)
        except Exception:  # noqa: BLE001
            pass
        deliver(out, folder, args)
        return

    if args.list or not args.show:
        _print_list()
        return

    preset = load_preset(args.show)
    mre = marker_re(preset)

    if args.text:
        full = args.text
    elif preset.get("source") == "news":
        print("Fetching live headlines & writing the bulletin (Gemini) \u2026")
        headlines = []
        try:
            headlines = make_bulletin.fetch_headlines()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("headline fetch failed: %s\n" % e)
        if headlines:
            full = (write_show(make_bulletin._build_prompt(headlines))
                    or make_bulletin._fallback_bulletin(headlines))
        else:
            full = fallback_show(preset)
    else:
        print("Writing today's '%s' episode (Gemini) \u2026" % preset.get("name", args.show))
        full = write_show(build_prompt(preset)) or fallback_show(preset)

    if args.script_only:
        print(display(full, preset, mre))
        return

    segments = split_segments(full, mre)
    now = datetime.datetime.now()
    out = args.out or os.path.join(
        music_dir(preset),
        "%s_%s.mp3" % (preset["slug"], now.strftime("%Y-%m-%d_%H")))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out + ".txt", "w", encoding="utf-8") as f:
        f.write(display(full, preset, mre))

    if args.no_mix:
        backend, voice = engine_voice(preset)
        raw = os.path.join(tempfile.gettempdir(), "bouzid_%s.wav" % preset["slug"])
        tts_engine.synthesize(clean(full, mre), raw, voice=voice or None)
        if out.endswith(".wav"):
            audio_mix._norm(raw, out)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                            "-codec:a", "libmp3lame", "-qscale:a", "2", out],
                           check=True)
    else:
        print("Mixing intro / bed / transitions / outro \u2026")
        build_show(preset, segments, out, params=overrides)
    print("Wrote", out)

    deliver(out, preset["upload_folder"], args)


if __name__ == "__main__":
    main()
