#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — generate a complete, mixed AI news bulletin (Google-only).

Pipeline:
  1. Pull the latest headlines from Tunisian RSS feeds.
  2. Ask Google Gemini to write a short Fus-ha bulletin using ONLY those
     headlines (falls back to a clean headline read-out if Gemini is
     unreachable, so news never silently fails).
  3. Synthesize speech with Google Gemini TTS.
  4. Mix: intro -> voice over a ducked music bed -> outro, using the mastered
     SFX imaging in assets/.
  5. Save to the news folder; optionally push to the station (AzuraCast).

Bouzid FM 2.0 is Google-only: text = Gemini, voice = Gemini TTS.

Env: GEMINI_API_KEY, GEMINI_TEXT_MODEL (default gemini-3.5-flash),
     GEMINI_TTS_MODEL, GEMINI_TTS_VOICE, MUSIC_DIR.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


# --- Load the project .env so direct CLI runs see the same config as the TUI --
# This dependency-free loader fills any KEY=VALUE from <project-root>/.env
# WITHOUT overwriting variables already set in the environment (so the TUI and
# explicit `export`s always win).
def _load_env_file():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    path = os.path.join(root, ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


_load_env_file()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import audio_mix  # noqa: E402
import tts_engine  # noqa: E402
import voice_fx    # noqa: E402  (broadcast "radio imaging" voice processor)

DEFAULT_FEEDS = [
    "https://www.tap.info.tn/ar/feed",
    "https://www.mosaiquefm.net/ar/rss",
    "https://www.tunisienumerique.com/feed/",
    "https://www.expressfm.com.tn/feed/",
]


def feeds():
    """Return RSS feed URLs (override via BOUZID_FEEDS, comma-separated)."""
    raw = (os.environ.get("BOUZID_FEEDS") or "").strip()
    if not raw:
        return list(DEFAULT_FEEDS)
    out = [u.strip() for u in raw.split(",") if u.strip()]
    return out or list(DEFAULT_FEEDS)


def fetch_headlines(limit=12, per_feed=6):
    """Fetch latest headlines from RSS feeds as (title, description) tuples."""
    items = []
    ok = 0
    errors = []
    for url in feeds():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BouzidFM/2.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
        except Exception as e:
            errors.append("%s: %s" % (url, str(e)[:120]))
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            errors.append("%s: XML parse error (%s)" % (url, str(e)[:80]))
            continue
        ok += 1
        cnt = 0
        for it in root.iter("item"):
            t = it.findtext("title") or ""
            d = it.findtext("description") or ""
            t = re.sub("<[^>]+>", "", t).strip()
            d = re.sub("<[^>]+>", "", d).strip()
            if t:
                items.append((t, d))
                cnt += 1
            if cnt >= per_feed:
                break

    if ok == 0:
        raise RuntimeError(
            "All RSS feeds failed (check internet / feed URLs). Sample errors: "
            + "; ".join(errors[:3]))

    seen, out = set(), []
    for t, d in items:
        k = t[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append((t, d))
        if len(out) >= limit:
            break
    return out


def _fallback_bulletin(headlines):
    """Build a clean Arabic bulletin WITHOUT any LLM (used if Gemini is down)."""
    lines = ["\u0645\u0633\u062a\u0645\u0639\u064a \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645\u060c \u0625\u0644\u064a\u0643\u0645 \u0623\u0628\u0631\u0632 \u0627\u0644\u0623\u062e\u0628\u0627\u0631."]
    for _i, (t, _d) in enumerate(headlines, 1):
        t = t.strip().rstrip(".\u060c")
        if t:
            lines.append("%s." % t)
    lines.append("\u0643\u0627\u0646\u062a \u0647\u0630\u0647 \u0646\u0634\u0631\u062a\u0643\u0645 \u0645\u0646 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645.")
    return "\n".join(lines)


def _build_prompt(headlines):
    src = "\n".join("- %s. %s" % (t, d) for t, d in headlines)
    return (
        "\u0623\u0646\u062a \u0645\u062d\u0631\u0651\u0631 \u0646\u0634\u0631\u0629 \u0623\u062e\u0628\u0627\u0631 \u0641\u064a \u0625\u0630\u0627\u0639\u0629 \"\u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645\" \u0627\u0644\u062a\u0648\u0646\u0633\u064a\u0629. "
        "\u0627\u0643\u062a\u0628 \u0646\u0634\u0631\u0629 \u0625\u062e\u0628\u0627\u0631\u064a\u0629 \u0642\u0635\u064a\u0631\u0629 \u0628\u0627\u0644\u0639\u0631\u0628\u064a\u0629 \u0627\u0644\u0641\u0635\u062d\u0649 (\u062d\u0648\u0627\u0644\u064a 150-220 \u0643\u0644\u0645\u0629) "
        "\u0627\u0639\u062a\u0645\u0627\u062f\u0627\u064b \u062d\u0635\u0631\u064a\u0627\u064b \u0639\u0644\u0649 \u0627\u0644\u0639\u0646\u0627\u0648\u064a\u0646 \u0627\u0644\u062a\u0627\u0644\u064a\u0629\u060c \u062f\u0648\u0646 \u0625\u0636\u0627\u0641\u0629 \u0623\u064a \u0645\u0639\u0644\u0648\u0645\u0629 \u0645\u0646 \u062e\u0627\u0631\u062c\u0647\u0627. "
        "\u0627\u0628\u062f\u0623 \u0628\u062a\u062d\u064a\u0629 \"\u0645\u0633\u062a\u0645\u0639\u064a \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645\u060c \u0625\u0644\u064a\u0643\u0645 \u0623\u0628\u0631\u0632 \u0627\u0644\u0623\u062e\u0628\u0627\u0631\" "
        "\u0648\u0627\u062e\u062a\u0645 \u0628\"\u0643\u0627\u0646\u062a \u0647\u0630\u0647 \u0646\u0634\u0631\u062a\u0643\u0645 \u0645\u0646 \u0628\u0648\u0632\u064a\u062f \u0625\u0641 \u0625\u0645\". \u0627\u062c\u0639\u0644\u0647\u0627 \u0633\u0644\u0633\u0629 \u0648\u062c\u0627\u0647\u0632\u0629 \u0644\u0644\u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0635\u0648\u062a\u064a\u0629.\n\n"
        "\u0627\u0644\u0639\u0646\u0627\u0648\u064a\u0646:\n" + src
    )


def _write_via_gemini(prompt):
    """Google Gemini text API (generativelanguage.googleapis.com).

    Needs GEMINI_API_KEY. Uses GEMINI_TEXT_MODEL (default gemini-3.5-flash),
    which handles Arabic very well. Returns the written text, or None on
    failure so the caller can fall back to the headline read-out.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    model = (os.environ.get("GEMINI_TEXT_MODEL", "").strip()
             or "gemini-3.5-flash")
    base = os.environ.get(
        "GEMINI_API_URL",
        "https://generativelanguage.googleapis.com/v1beta/models")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }
    url = base.rstrip("/") + "/" + model + ":generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("x-goog-api-key", api_key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write("Gemini text model %s failed (%s).\n" % (model, e))
        return None
    try:
        cands = data.get("candidates") or []
        parts = (cands[0].get("content", {}) or {}).get("parts", []) or []
        # Skip any reasoning "thought" parts; keep only spoken text.
        text = "".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        text = ""
    text = re.sub(r"<think>.*?</think>", "", text,
                  flags=re.DOTALL | re.IGNORECASE).strip()
    if text:
        sys.stderr.write("Written by Gemini model: %s\n" % model)
        return text
    sys.stderr.write("Gemini returned no text (model %s).\n" % model)
    return None


def write_bulletin(headlines, **_ignored):
    """Write the bulletin with Gemini, falling back to a headline read-out."""
    out = _write_via_gemini(_build_prompt(headlines))
    if out:
        return out
    sys.stderr.write("Gemini unreachable; using headline read-out instead.\n")
    return _fallback_bulletin(headlines)


def synth(text, out_wav):
    """Synthesize via the Google Gemini TTS engine."""
    return tts_engine.synthesize(text, out_wav)


def pick_asset(folder):
    d = os.path.join(ROOT, "assets", folder)
    if not os.path.isdir(d):
        return None
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith((".mp3", ".wav", ".m4a", ".ogg", ".flac")):
            return os.path.join(d, fn)
    return None


def music_news_dir():
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    d = os.path.join(md, "news")
    os.makedirs(d, exist_ok=True)
    return d


def main():
    ap = argparse.ArgumentParser(description="Generate a mixed Bouzid FM bulletin")
    ap.add_argument("--out", default=None)
    ap.add_argument("--text", help="skip the LLM and speak this text")
    ap.add_argument("--script-only", action="store_true",
                    help="print the bulletin text and exit")
    ap.add_argument("--no-mix", action="store_true",
                    help="raw voice only (no bed / intro / outro)")
    ap.add_argument("--bed", default=None)
    ap.add_argument("--intro", default=None)
    ap.add_argument("--outro", default=None)
    ap.add_argument("--upload", action="store_true", help="push to AzuraCast")
    ap.add_argument("--air", action="store_true",
                    help="upload then cut it onto the air immediately")
    ap.add_argument("--play", action="store_true", help="play the result (ffplay)")
    args = ap.parse_args()

    if args.text:
        text = args.text
    else:
        hl = fetch_headlines()
        if not hl:
            sys.exit("No headlines fetched (check network / feeds).")
        print("Fetched %d headlines; writing bulletin (Gemini) ..." % len(hl))
        text = write_bulletin(hl)

    if args.script_only:
        print(text)
        return

    now = datetime.datetime.now()
    out = args.out or os.path.join(
        music_news_dir(), "bulletin_%s.mp3" % now.strftime("%Y-%m-%d_%H"))
    with open(out + ".txt", "w", encoding="utf-8") as f:
        f.write(text)

    raw = os.path.join(tempfile.gettempdir(), "bouzid_voice.wav")
    print("Synthesizing speech (Google Gemini TTS) ...")
    synth(text, raw)

    if (voice_fx.enabled()
            and os.environ.get("BOUZID_NEWS_VOICE_FX", "1").strip().lower()
            not in ("0", "false", "no", "off")):
        print("Shaping anchor voice (broadcast chain) ...")
        fx = os.path.join(tempfile.gettempdir(), "bouzid_voice_fx.wav")
        voice_fx.process(raw, fx)
        raw = fx

    if args.no_mix:
        if out.endswith(".wav"):
            audio_mix._norm(raw, out)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                            "-codec:a", "libmp3lame", "-qscale:a", "2", out],
                           check=True)
    else:
        print("Mixing intro / bed / outro ...")
        audio_mix.build(
            raw, out,
            bed=args.bed or pick_asset("beds"),
            intro=args.intro or pick_asset("intro"),
            outro=args.outro or pick_asset("outro"),
        )
    print("Wrote", out)

    if args.upload or args.air:
        import azuracast
        ac = azuracast.AzuraCast()
        dest = "News/" + os.path.basename(out)
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
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", out])


if __name__ == "__main__":
    main()
