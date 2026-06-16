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

Usage:
    python3 scripts/music_reset.py            # reconcile + re-import + reschedule
    python3 scripts/music_reset.py --dry-run  # just list what would be removed
    python3 scripts/music_reset.py --no-import --no-schedule
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import azuracast  # noqa: E402

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
              ".flac", ".wav", ".wma")


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
    """Music-pool folder names, read from the schedule config (single source)."""
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
    ac = azuracast.AzuraCast()

    try:
        remote = ac.files()
    except RuntimeError as e:
        sys.exit("Cannot reach AzuraCast: %s" % e)

    local = local_relpaths(root, crates)
    orphans = []
    for f in remote:
        rp = (f.get("path") or "").lstrip("/")
        if not rp or "/" not in rp:
            continue
        top = rp.split("/")[0].lower()
        if top in crates and rp.lower() not in local:
            orphans.append(f)

    print("Music library : %s" % root)
    print("Local tracks  : %d  (crates: %s)"
          % (len(local), ", ".join(sorted(crates))))
    print("Orphans found : %d AzuraCast music track(s) not in your library"
          % len(orphans))

    if args.dry_run:
        for f in orphans:
            print("  would remove:", f.get("path"))
        print("\n(dry-run — nothing was changed)")
        return 0

    removed = failed = 0
    for f in orphans:
        fid = f.get("id")
        if fid is None:
            continue
        try:
            ac.delete_file(fid)
            removed += 1
            print("  ✗ removed", f.get("path"))
        except RuntimeError as e:
            failed += 1
            print("  ⚠ could not remove %s (%s)"
                  % (f.get("path"), str(e)[:120]))
    print("Removed %d orphan(s), %d failed." % (removed, failed))

    if not args.no_import:
        print("\nImporting current local music ...")
        try:
            ac.import_dir(root)
        except RuntimeError as e:
            print("⚠ import failed:", e)

    if not args.no_schedule:
        print("\nRe-applying the weekly schedule ...")
        try:
            ac.apply_schedule(reset=True)
            ac.restart_broadcasting()
        except RuntimeError as e:
            print("⚠ schedule/restart failed:", e)

    print("\nDone. AzuraCast now uses only your downloaded music. "
          "Check with: bouzid now")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
