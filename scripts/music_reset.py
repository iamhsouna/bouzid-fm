#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — reset AzuraCast's music pool to match your local library.

AzuraCast keeps its OWN copy of every track (in its Docker media storage), so
deleting a song from your local music folder does NOT remove it from the air —
the old copy keeps playing. This tool reconciles the two: it deletes every
track in AzuraCast's music-crate folders (morning/daytime/.../weekend) that no
longer exists in your local MUSIC_DIR, re-imports anything new, then re-applies
the weekly schedule so only your current downloaded music is on air.

It ONLY touches the music crate folders. News, Jingles, Cook and other show
folders are left completely alone.

Every phase prints a timestamped line so you can always see what it is doing —
fetching AzuraCast's full media list is the slow, but normal, first step.

Usage:
    python3 scripts/music_reset.py            # reconcile + re-import + reschedule
    python3 scripts/music_reset.py --dry-run  # just list what would be removed
    python3 scripts/music_reset.py --no-import --no-schedule
"""
import argparse
import importlib.util
import os
import sys
import time

# Stream output immediately so you always see progress (no silent hang).
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import azuracast  # noqa: E402

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
              ".flac", ".wav", ".wma")

_T0 = time.monotonic()


def log(msg=""):
    print(msg, flush=True)


def step(msg):
    print("[%6.1fs] %s" % (time.monotonic() - _T0, msg), flush=True)


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


def crate_folders():
    """Music-pool folder names — auto-derived from non-empty folders on disk.

    The single source of truth is now a runtime scan of MUSIC_DIR (via
    azuracast.discover_music_crates): any folder holding >=1 audio file is a
    crate. This keeps orphan-detection honest — delete the last file from a
    folder and a subsequent `music-reset` will purge that folder's stale
    remote tracks too. Falls back to the schedule_config folder list only if
    the disk scan returns nothing (e.g. MUSIC_DIR not mounted locally).
    """
    try:
        found = azuracast.discover_music_crates(music_root())
        if found:
            return {f.lower() for f in found}
    except Exception:  # noqa: BLE001
        pass
    # Fallback: the full configured crate set from schedule_config.py.
    path = os.path.join(ROOT, "config", "schedule_config.py")
    try:
        spec = importlib.util.spec_from_file_location("schedule_config", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        folders = {(v.get("folder") or "").lower()
                   for v in mod.PLAYLISTS.values()}
        folders.discard("")
        if folders:
            return folders
    except Exception:  # noqa: BLE001
        pass
    return {"morning", "daytime", "afternoon", "evening", "night",
            "overnight", "friday", "weekend"}


def local_relpaths(root, crates):
    """Lowercased 'crate/file.ext' paths present locally, limited to crates."""
    found = set()
    for crate in crates:
        d = os.path.join(root, crate)
        if not os.path.isdir(d):
            continue
        for base, _dirs, names in os.walk(d):
            for n in names:
                if n.lower().endswith(AUDIO_EXTS) and not n.startswith("."):
                    rel = os.path.relpath(os.path.join(base, n), root)
                    found.add(rel.replace(os.sep, "/").lower())
    return found


def main():
    ap = argparse.ArgumentParser(
        description="Reset AzuraCast's music pool to match local MUSIC_DIR")
    ap.add_argument("--dry-run", action="store_true",
                    help="list orphan tracks that would be removed; change nothing")
    ap.add_argument("--no-import", action="store_true",
                    help="do not upload new local tracks, only remove orphans")
    ap.add_argument("--no-schedule", action="store_true",
                    help="do not re-apply the weekly schedule afterwards")
    args = ap.parse_args()

    root = music_root()
    crates = crate_folders()
    step("Music library : %s" % root)
    step("Crates        : %s" % ", ".join(sorted(crates)))

    step("Scanning your local music ...")
    local = local_relpaths(root, crates)
    step("Local tracks  : %d" % len(local))

    step("Connecting to AzuraCast ...")
    ac = azuracast.AzuraCast()
    step("Fetching the AzuraCast media list (normal slow step on big libraries) ...")
    try:
        remote = ac.files()
    except RuntimeError as e:
        sys.exit("Cannot reach AzuraCast: %s" % e)
    step("AzuraCast has : %d media file(s) total" % len(remote))

    orphans = []
    for f in remote:
        rp = (f.get("path") or "").lstrip("/")
        if not rp or "/" not in rp:
            continue
        top = rp.split("/")[0].lower()
        if top in crates and rp.lower() not in local:
            orphans.append(f)
    step("Orphans       : %d music track(s) in AzuraCast but not in your library"
         % len(orphans))

    if args.dry_run:
        for f in orphans:
            log("  would remove: %s" % f.get("path"))
        step("dry-run complete — nothing was changed.")
        return 0

    if not orphans:
        step("Nothing to remove.")
    else:
        n = len(orphans)
        removed = failed = 0
        for i, f in enumerate(orphans, 1):
            fid = f.get("id")
            if fid is None:
                continue
            try:
                ac.delete_file(fid)
                removed += 1
                log("  [%d/%d] ✗ removed %s" % (i, n, f.get("path")))
            except RuntimeError as e:
                failed += 1
                log("  [%d/%d] ⚠ could not remove %s (%s)"
                    % (i, n, f.get("path"), str(e)[:120]))
        step("Removed %d orphan(s), %d failed." % (removed, failed))

    if not args.no_import:
        step("Importing any new local tracks ...")
        try:
            ac.import_dir(root)
        except RuntimeError as e:
            log("⚠ import failed: %s" % e)

    if not args.no_schedule:
        step("Re-applying the weekly schedule ...")
        try:
            ac.apply_schedule(reset=True)
            step("Restarting broadcasting so changes go live ...")
            ac.restart_broadcasting()
        except RuntimeError as e:
            log("⚠ schedule/restart failed: %s" % e)

    step("Done. AzuraCast now uses only your downloaded music. "
         "Check with: bouzid now")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
