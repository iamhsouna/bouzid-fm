#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bouzid FM - Anghami music fetcher (Anghami tracklist -> MP3 via yt-dlp).

Why Anghami?  YouTube search alone surfaces a lot of 18+ / low-quality clips,
especially for Tunisian music. Anghami gives a clean, curated tracklist per
mood; we read that tracklist (public playlists, no login) and download the
matching audio from YouTube with yt-dlp. So: Anghami = curation, YouTube =
audio.

Each \"crate\" is a music folder used by the weekly schedule
(config/schedule_config.py). The crate -> Anghami playlist mapping lives in
config/music_playlists.json. Every crate has a researched 'default' playlist
and an optional 'override' you can set from the Music tab (or with --set).

Progress is written to data/fetch-status.json in the SAME shape used by the
old fetcher, so `bouzid fetch-status` / the Music tab status button keep
working. A track is skipped only when its tidy file is already present on
disk, so re-running tops a crate up instead of re-downloading -- and anything
missing always comes back (no reliance on a stale yt-dlp download archive).

No Python deps - shells out to the `yt-dlp` (and `ffmpeg`) binaries.

Usage:
    python3 anghami_dl.py --all                 # every crate (its playlist)
    python3 anghami_dl.py --crate morning       # one crate
    python3 anghami_dl.py --list                # show crate -> playlist plan
    python3 anghami_dl.py --get morning         # print the effective URL
    python3 anghami_dl.py --set morning <url>   # set an override (\"\" resets)
    python3 anghami_dl.py --reset morning       # clear the override
    python3 anghami_dl.py --clear morning       # delete that crate's MP3s
    python3 anghami_dl.py --crate morning --url <url>   # one-off URL
    python3 anghami_dl.py --file songs.txt --crate night  # from a text file
    python3 anghami_dl.py --all --dry-run       # print, download nothing
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_FILE = os.path.join(ROOT, "config", "music_playlists.json")
STATUS_FILE = os.path.join(ROOT, "data", "fetch-status.json")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ----------------------------------------------------------------------- #
#  Config helpers
# ----------------------------------------------------------------------- #
def load_cfg():
    with open(CFG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg):
    tmp = CFG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_FILE)


def effective_url(spec):
    """override wins over default."""
    ov = (spec.get("override") or "").strip()
    return ov or (spec.get("default") or "").strip()


def music_dir():
    # MUSIC_DIR may be relative to repo root (matches docker-compose default).
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    return md if os.path.isabs(md) else os.path.join(ROOT, md)


def quality(cfg, override=None):
    q = override or os.environ.get("MUSIC_QUALITY") or cfg.get("quality") or "128"
    return str(q).split("#", 1)[0].strip() or "128"


# ----------------------------------------------------------------------- #
#  Anghami tracklist extraction (public playlists, JSON-LD)
# ----------------------------------------------------------------------- #
def fetch_anghami_playlist(url):
    """Return (playlist_name, [(name, artist), ...]) from an Anghami URL."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    script_re = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL)
    for script in script_re.findall(html):
        script = script.strip()
        if not (script.startswith("{") or script.startswith("[")):
            continue
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            if entry.get("@type") != "MusicPlaylist":
                continue
            tracks = entry.get("track", []) or []
            songs = []
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                if t.get("@type") not in ("MusicRecording", None):
                    continue
                name = (t.get("name") or "").strip()
                artist = ""
                by = t.get("byArtist")
                if isinstance(by, dict):
                    artist = (by.get("name") or "").strip()
                elif isinstance(by, list) and by:
                    artist = (by[0].get("name") or "").strip()
                if name:
                    songs.append((name, artist))
            playlist_name = entry.get("name", "playlist")
            return playlist_name, songs
    raise RuntimeError(
        "Could not find playlist data on the page. Make sure the URL is a "
        "public Anghami playlist."
    )


def read_songs_from_file(filepath):
    """Read a song list from a text file (one 'Song - Artist' per line)."""
    songs = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if " - " in line:
                a, b = line.split(" - ", 1)
                songs.append((a.strip(), b.strip()))
            else:
                songs.append((line, ""))
    return songs


# ----------------------------------------------------------------------- #
#  Status file (compatible with scripts/fetch_status.py)
# ----------------------------------------------------------------------- #
def write_status(status):
    status["updated_at"] = time.time()
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_FILE)
    except Exception:  # noqa: BLE001
        pass


def count_mp3s(md):
    n = 0
    for _r, _d, files in os.walk(md):
        n += sum(1 for f in files if f.lower().endswith(".mp3"))
    return n


def bar(done, total, width=28):
    frac = (done / total) if total else 0.0
    frac = max(0.0, min(1.0, frac))
    filled = int(round(width * frac))
    return "[" + "\u2588" * filled + "\u2591" * (width - filled) + \
        "] %3d%%" % int(frac * 100)


def render(status):
    if not sys.stderr.isatty():
        return
    si, st = status["source_index"], status["sources_total"]
    line = ("\r  %s  track %d/%d  -  %d in library  -  %s: %s"
            % (bar(si, st), si, st, status["tracks_done"],
               status["crate"], status["label"][:42]))
    sys.stderr.write(line[:160].ljust(160))
    sys.stderr.flush()


# ----------------------------------------------------------------------- #
#  Downloading
# ----------------------------------------------------------------------- #
def clean_track_name(song_name, artist, maxlen=70):
    """Build a tidy, archive-friendly file name from the clean Anghami metadata
    (artist + title) instead of the noisy YouTube video title. Turns names like
    "Balti - Ya Lili (Official Music Video) || HD [dQw4w9WgXcQ].mp3" into a neat
    "Balti - Ya Lili.mp3"."""
    artist = (artist or "").strip()
    song = (song_name or "").strip()
    name = ("%s - %s" % (artist, song)) if (artist and song) else (artist or song or "track")
    # Drop bracketed noise like "(Official Video)", "[HD]", "[videoid]".
    name = re.sub(r"[\(\[\{][^)\]\}]*"
                  r"(official|video|audio|lyric|clip|hd|4k|mv|remaster|prod)"
                  r"[^)\]\}]*[\)\]\}]", "", name, flags=re.I)
    # Strip leftover bracket groups (e.g. a bare "[K5M574HCTRM]" video id).
    name = re.sub(r"[\(\[\{][^)\]\}]*[\)\]\}]", "", name)
    # Replace characters that are unsafe or ugly in file names.
    name = re.sub(r"[\\/:*?\"<>|\uFF5C\[\]\{\}\u3010\u3011]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(" .-_")
    return name or "track"


def dest_path(outdir, song_name, artist):
    """The deterministic, tidy MP3 path a track will be saved to."""
    return os.path.join(outdir, clean_track_name(song_name, artist) + ".mp3")


def ytdlp_cmd(song_name, artist, outdir, q):
    query = ("%s %s" % (song_name, artist)).strip()
    # Name the saved file from the clean Anghami metadata, not the messy YT
    # title, and don't clobber an existing file with the same tidy name.
    outname = clean_track_name(song_name, artist) + ".%(ext)s"
    # NOTE: deliberately NO --download-archive. The archive lived in the music
    # library (which survives reinstalls) and would skip tracks as "already
    # recorded" even after their files were wiped. We gate on real on-disk
    # presence instead (see download_song / the main loop).
    return [
        "yt-dlp",
        "--ignore-errors", "--no-warnings", "--newline",
        "--no-playlist",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", q,
        "--embed-thumbnail", "--add-metadata", "--embed-metadata",
        "--match-filter", "!is_live",
        "--retries", "3",
        "--no-overwrites",
        "--postprocessor-args", "ffmpeg:-ar 44100 -ac 2",
        "-o", os.path.join(outdir, outname),
        "ytsearch1:%s" % query,
    ]


def download_song(song_name, artist, outdir, q, status, quiet):
    dest = dest_path(outdir, song_name, artist)
    cmd = ytdlp_cmd(song_name, artist, outdir, q)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
    except FileNotFoundError:
        sys.exit("yt-dlp not found. Install it (the installer does this).")
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not quiet:
            print(line, flush=True)
    proc.wait()
    # Success is decided by whether the tidy file is actually on disk now -
    # not by parsing yt-dlp output and not by a stale download archive.
    got = os.path.exists(dest)
    if got:
        status["tracks_done"] += 1
        write_status(status)
        render(status)
    return got


def rescan_library():
    """Best-effort import + schedule so new music goes live. Skips silently if
    AzuraCast isn't configured/reachable."""
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
        except Exception:  # noqa: BLE001
            pass


def clear_crate(crate, md):
    """Delete the MP3s in one crate folder. Returns the number of files removed.
    Also drops the legacy yt-dlp download archive if present (it is no longer
    used, but old installs may still have one lying around)."""
    outdir = os.path.join(md, crate)
    if not os.path.isdir(outdir):
        print("Nothing to clear: %s does not exist." % outdir)
        return 0
    mp3s = glob.glob(os.path.join(outdir, "*.mp3"))
    for p in mp3s:
        try:
            os.remove(p)
        except OSError:
            pass
    # Best-effort cleanup of the obsolete archive so nothing stale lingers.
    legacy_archive = os.path.join(md, ".archive.txt")
    try:
        if os.path.exists(legacy_archive):
            os.remove(legacy_archive)
    except OSError:
        pass
    print("Cleared %d track(s) from %s/" % (len(mp3s), crate))
    return len(mp3s)


# ----------------------------------------------------------------------- #
#  Plan building / main
# ----------------------------------------------------------------------- #
def build_plan(cfg, crates, url_override=None, file_path=None):
    """Return (plan, music_dir). plan = list of dicts: crate, name, artist."""
    md = music_dir()
    plan = []
    for crate in crates:
        outdir = os.path.join(md, crate)
        os.makedirs(outdir, exist_ok=True)
        if file_path:
            songs = read_songs_from_file(file_path)
            src = os.path.basename(file_path)
        else:
            url = url_override or effective_url(cfg["crates"][crate])
            if not url:
                print("  ! %s has no playlist set - skipping" % crate)
                continue
            print("Fetching Anghami playlist for '%s': %s" % (crate, url))
            try:
                pname, songs = fetch_anghami_playlist(url)
            except Exception as e:  # noqa: BLE001
                print("  ! %s: %s" % (crate, e))
                continue
            src = pname
            print("  '%s' -> %d track(s)" % (pname, len(songs)))
        for name, artist in songs:
            plan.append({"crate": crate, "name": name, "artist": artist,
                         "src": src})
    return plan, md


def resolve_crates(cfg, args):
    keys = list(cfg["crates"].keys())
    if args.all:
        return keys
    if args.crate:
        if args.crate not in cfg["crates"]:
            sys.exit("Unknown crate '%s'. Known: %s"
                     % (args.crate, ", ".join(keys)))
        return [args.crate]
    return []


def main():
    ap = argparse.ArgumentParser(
        description="Fetch Bouzid FM music from Anghami playlists via yt-dlp.")
    ap.add_argument("--all", action="store_true", help="every crate")
    ap.add_argument("--crate", help="only this crate/folder")
    ap.add_argument("--url", help="one-off Anghami URL (with --crate)")
    ap.add_argument("-f", "--file", help="text file 'Song - Artist' per line")
    ap.add_argument("-q", "--quality", help="audio quality kbps (default 128)")
    ap.add_argument("--list", action="store_true", help="print plan and exit")
    ap.add_argument("--get", metavar="CRATE", help="print effective URL")
    ap.add_argument("--set", nargs=2, metavar=("CRATE", "URL"),
                    help="set an override URL ('' resets to default)")
    ap.add_argument("--reset", metavar="CRATE", help="clear a crate's override")
    ap.add_argument("--clear", metavar="CRATE",
                    help="delete a crate's downloaded MP3s")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="print what would download, download nothing")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress yt-dlp output; progress bar only")
    args = ap.parse_args()

    cfg = load_cfg()

    # --- config-only sub-commands ------------------------------------- #
    if args.get:
        if args.get not in cfg["crates"]:
            sys.exit("Unknown crate '%s'" % args.get)
        print(effective_url(cfg["crates"][args.get]))
        return
    if args.set:
        crate, url = args.set
        if crate not in cfg["crates"]:
            sys.exit("Unknown crate '%s'" % crate)
        cfg["crates"][crate]["override"] = url.strip()
        save_cfg(cfg)
        print("Set %s -> %s" % (crate, effective_url(cfg["crates"][crate])))
        return
    if args.reset:
        if args.reset not in cfg["crates"]:
            sys.exit("Unknown crate '%s'" % args.reset)
        cfg["crates"][args.reset]["override"] = ""
        save_cfg(cfg)
        print("Reset %s -> %s"
              % (args.reset, effective_url(cfg["crates"][args.reset])))
        return
    if args.clear:
        if args.clear not in cfg["crates"]:
            sys.exit("Unknown crate '%s'" % args.clear)
        clear_crate(args.clear, music_dir())
        return

    if args.list:
        md = music_dir()
        crates = resolve_crates(cfg, args) or list(cfg["crates"].keys())
        for crate in crates:
            spec = cfg["crates"][crate]
            tag = " (override)" if (spec.get("override") or "").strip() else ""
            print("[%s] -> %s" % (crate, os.path.join(md, crate)))
            print("   %s%s" % (effective_url(spec), tag))
            print("   %s" % spec.get("label", ""))
        return

    crates = resolve_crates(cfg, args)
    if not crates:
        ap.error("choose --all or --crate NAME (or use --list / --get / --set)")

    if args.url and len(crates) != 1:
        ap.error("--url needs exactly one --crate")

    if shutil.which("yt-dlp") is None and not args.dry_run:
        sys.exit("yt-dlp not found. Install it (the installer does this).")

    q = quality(cfg, args.quality)
    plan, md = build_plan(cfg, crates, url_override=args.url,
                          file_path=args.file)
    total = len(plan)
    if total == 0:
        print("Nothing to download.")
        return

    status = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "updated_at": time.time(),
        "sources_total": total,
        "source_index": 0,
        "crate": "",
        "label": "starting\u2026",
        "tracks_done": count_mp3s(md),
        "tracks_at_start": 0,
        "failures": 0,
        "finished": False,
        "music_dir": md,
    }
    status["tracks_at_start"] = status["tracks_done"]
    write_status(status)

    print("\nMusic dir: %s" % md)
    print("Quality  : %s kbps" % q)
    print("%d track(s) to fetch across %d crate(s)\n" % (total, len(crates)))

    failures = 0
    for i, item in enumerate(plan, 1):
        status["source_index"] = i
        status["crate"] = item["crate"]
        label = ("%s - %s" % (item["name"], item["artist"])
                 if item["artist"] else item["name"])
        status["label"] = label
        write_status(status)
        render(status)
        if not args.quiet:
            print("\n[%d/%d] (%s) %s" % (i, total, item["crate"], label))
        if args.dry_run:
            print("   ytsearch1:%s"
                  % ("%s %s" % (item["name"], item["artist"])).strip())
            continue
        outdir = os.path.join(md, item["crate"])
        # Skip only when the tidy file is REALLY on disk - this is what makes
        # "top up" work without re-downloading, while never skipping a track
        # whose file is missing (the old stale-archive bug).
        dest = dest_path(outdir, item["name"], item["artist"])
        if os.path.exists(dest):
            if not args.quiet:
                print("   already in library -> %s" % os.path.basename(dest))
            continue
        try:
            ok = download_song(item["name"], item["artist"], outdir, q,
                               status, args.quiet)
        except KeyboardInterrupt:
            status["finished"] = True
            status["interrupted"] = True
            write_status(status)
            print("\nInterrupted. Progress saved - re-run to resume "
                  "(already-downloaded tracks are skipped).")
            return
        if not ok:
            failures += 1
            status["failures"] = failures
            write_status(status)

    if not args.dry_run:
        status["finished"] = True
        write_status(status)
        rescan_library()
        added = status["tracks_done"] - status["tracks_at_start"]
        if sys.stderr.isatty():
            sys.stderr.write("\n")
        print("\nDone. %d/%d tracks ok - %d new track(s) - %d total in library."
              % (total - failures, total, added, status["tracks_done"]))


if __name__ == "__main__":
    main()
