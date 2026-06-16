#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bouzid FM — trending Tunisian music fetcher (YouTube -> MP3 via yt-dlp).

Reads config/crates.json. For each crate (= music folder used by the weekly
schedule) it runs the configured yt-dlp searches / playlists, downloads the
top tracks as tagged MP3s, skips anything already grabbed (download archive),
and (if AzuraCast is configured) imports them + applies the schedule when done.

While running it prints a live progress bar AND writes a machine-readable
status file (data/fetch-status.json) so you can check progress at any time
— even when this runs in the background — with:  bouzid fetch-status

No Python deps — shells out to the `yt-dlp` and `ffmpeg` binaries.

Usage:
    python3 fetch_trending.py                 # all crates
    python3 fetch_trending.py --crate daytime # one crate
    python3 fetch_trending.py --list          # show crate plan
    python3 fetch_trending.py --dry-run       # print commands only
    python3 fetch_trending.py --quiet         # only progress bar (no yt-dlp spam)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CRATES_FILE = os.path.join(ROOT, "config", "crates.json")
STATUS_FILE = os.path.join(ROOT, "data", "fetch-status.json")


def load_cfg():
    with open(CRATES_FILE, encoding="utf-8") as f:
        return json.load(f)


def music_dir():
    # MUSIC_DIR may be relative to repo root (matches docker-compose default)
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    return md if os.path.isabs(md) else os.path.join(ROOT, md)


def _envint(name, default):
    """Read an int env var, tolerating inline '# comments' and whitespace
    (systemd EnvironmentFile keeps everything after '=' as the value)."""
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    raw = raw.split("#", 1)[0].strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def build_plan(cfg, only=None):
    """Return a list of {crate, src, label, cmd} dicts and the music dir."""
    md = music_dir()
    archive = os.path.join(md, ".archive.txt")
    max_n = _envint("MAX_PER_SOURCE", cfg.get("max_per_source", 15))
    max_dur = _envint("MAX_DURATION_SEC", cfg.get("max_duration_sec", 600))
    geo = cfg.get("geo_country", "TN")
    plan = []
    for crate, spec in cfg["crates"].items():
        if only and crate != only:
            continue
        outdir = os.path.join(md, crate)
        os.makedirs(outdir, exist_ok=True)
        sources = [("search", q, f"ytsearch{max_n}:{q}") for q in spec.get("queries", [])]
        sources += [("playlist", p, p) for p in spec.get("playlists", [])]
        for kind, label, src in sources:
            cmd = [
                "yt-dlp",
                "--ignore-errors", "--no-warnings",
                "--newline",                      # one progress line per update
                "--geo-bypass-country", geo,
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
                "--embed-thumbnail", "--add-metadata",
                "--embed-metadata",
                "--download-archive", archive,
                "--match-filter", f"duration < {max_dur} & !is_live",
                "--no-playlist-reverse",
                "--retries", "3",
                "--postprocessor-args", "ffmpeg:-ar 44100 -ac 2",
                "-o", os.path.join(outdir, "%(title).80s [%(id)s].%(ext)s"),
                src,
            ]
            plan.append({"crate": crate, "kind": kind, "label": label, "cmd": cmd})
    return plan, md


def count_mp3s(md):
    """Count MP3s in the music library.

    This can be expensive on large libraries, so callers should avoid using it
    repeatedly while a download is running.
    """
    n = 0
    for _root, _dirs, files in os.walk(md):
        # mp3 only (that's what yt-dlp writes here)
        n += sum(1 for f in files if f.lower().endswith(".mp3"))
    return n


def write_status(status):
    status["updated_at"] = time.time()
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass


def bar(done, total, width=28):
    frac = (done / total) if total else 0.0
    frac = max(0.0, min(1.0, frac))
    filled = int(round(width * frac))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {int(frac * 100):3d}%"


def render(status):
    """Draw the live progress bar on a single rewritten terminal line."""
    if not sys.stderr.isatty():
        return
    si, st = status["source_index"], status["sources_total"]
    line = (f"\r  {bar(si, st)}  source {si}/{st}  •  "
            f"{status['tracks_done']} tracks  •  {status['crate']}: "
            f"{status['label'][:42]}")
    sys.stderr.write(line[:160].ljust(160))
    sys.stderr.flush()


def rescan_library():
    """Best-effort: import freshly downloaded tracks into AzuraCast and apply
    the weekly schedule so new music goes live. Silently skips if AzuraCast
    isn't configured/reachable (e.g. before the one-time web setup)."""
    if not os.environ.get("AZURACAST_API_KEY"):
        return
    azuracast = os.path.join(HERE, "azuracast.py")
    if not os.path.exists(azuracast):
        return
    for cmd in (
        [sys.executable, azuracast, "import", "--dir", music_dir()],
        [sys.executable, azuracast, "apply-schedule", "--reset"],
        [sys.executable, azuracast, "restart"],
    ):
        try:
            subprocess.run(cmd, check=False, timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def run_source(item, status, md, quiet):
    """Run one yt-dlp source, streaming output and updating progress."""
    proc = subprocess.Popen(
        item["cmd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        # A finished track produces an ExtractAudio destination line.
        if "[ExtractAudio] Destination:" in line or line.startswith("[download] 100%"):
            if "[ExtractAudio] Destination:" in line:
                # Fast path: update the track count incrementally.
                # Avoid re-scanning the full music directory (can be huge).
                status["tracks_done"] += 1
                write_status(status)
                render(status)
        if not quiet:
            # Keep the raw yt-dlp log (useful in the background log file).
            print(line, flush=True)
    proc.wait()
    return proc.returncode


def main():
    ap = argparse.ArgumentParser(description="Fetch trending Tunisian music")
    ap.add_argument("--crate", help="only this crate/folder")
    ap.add_argument("--list", action="store_true", help="print plan and exit")
    ap.add_argument("--dry-run", action="store_true", help="print commands only")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress yt-dlp output; show only the progress bar")
    args = ap.parse_args()

    if shutil.which("yt-dlp") is None and not (args.list or args.dry_run):
        sys.exit("yt-dlp not found. Install it (the installer does this).")

    cfg = load_cfg()
    plan, md = build_plan(cfg, only=args.crate)

    if args.list:
        for crate, spec in cfg["crates"].items():
            if args.crate and crate != args.crate:
                continue
            print(f"[{crate}] -> {os.path.join(md, crate)}")
            for q in spec.get("queries", []):
                print(f"   search: {q}")
            for p in spec.get("playlists", []):
                print(f"   playlist: {p}")
        return

    total = len(plan)
    status = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "updated_at": time.time(),
        "sources_total": total,
        "source_index": 0,
        "crate": "",
        "label": "starting…",
        # IMPORTANT: only count once (walk can be slow on big libraries).
        "tracks_done": count_mp3s(md),
        "tracks_at_start": 0,
        "failures": 0,
        "finished": False,
        "music_dir": md,
    }
    status["tracks_at_start"] = status["tracks_done"]
    write_status(status)

    print(f"Music dir: {md}")
    print(f"{total} source(s) to fetch\n")

    failures = 0
    for i, item in enumerate(plan, 1):
        status["source_index"] = i
        status["crate"] = item["crate"]
        status["label"] = item["label"]
        write_status(status)
        render(status)
        if not args.quiet:
            print(f"\n[{i}/{total}] ({item['crate']}) {item['label']}")
        if args.dry_run:
            print("   " + " ".join(item["cmd"]))
            continue
        try:
            rc = run_source(item, status, md, args.quiet)
        except KeyboardInterrupt:
            status["finished"] = True
            status["interrupted"] = True
            write_status(status)
            print("\nInterrupted. Progress saved — re-run `bouzid fetch` to resume "
                  "(already-downloaded tracks are skipped).")
            return
        if rc != 0:
            failures += 1
            status["failures"] = failures
            write_status(status)

    if not args.dry_run:
        # tracks_done has already been incremented live. Don't rescan.
        status["finished"] = True
        write_status(status)
        rescan_library()
        added = status["tracks_done"] - status["tracks_at_start"]
        if sys.stderr.isatty():
            sys.stderr.write("\n")
        print(f"\nDone. {total - failures}/{total} sources ok • "
              f"{added} new track(s) • {status['tracks_done']} total in library.")


if __name__ == "__main__":
    main()
