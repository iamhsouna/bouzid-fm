#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — imaging asset library + resolver (shared by everything).

UNIFORM STRUCTURE (one folder per show, one sub-folder per kind):

    assets/<show>/intro/
    assets/<show>/outro/
    assets/<show>/bed/
    assets/<show>/transition/
    assets/<show>/sting/

"<show>" is either a preset slug (shows/<slug>.json) OR the special "station"
show, which holds the shared/default imaging used as a fallback and for the
jingle beds. Every show — station included — lives under its own folder; there
is no more top-level assets/intro or assets/beds.

Each kind can hold MANY generated variants side by side. Resolution rule:
    * If the user pinned a DEFAULT variant for that (show, kind), use it.
    * Otherwise use the most recently generated (newest mtime) variant.

Pinned defaults live in assets/imaging_defaults.json as a flat map of
"<show>/<kind>" -> filename. Delete the entry (clear_default) to go back to
"always use the latest".

Import-friendly (show_engine, audio_mix, make_imaging, the TUI) AND a small CLI:

    python3 scripts/assets_lib.py shows
    python3 scripts/assets_lib.py list <show> <kind> [--json]
    python3 scripts/assets_lib.py resolve <show> <kind>
    python3 scripts/assets_lib.py set-default <show> <kind> <filename>
    python3 scripts/assets_lib.py clear-default <show> <kind>
    python3 scripts/assets_lib.py delete <show> <kind> <filename>
    python3 scripts/assets_lib.py overview [--json]
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")
SHOWS_DIR = os.path.join(ROOT, "shows")
DEFAULTS_FILE = os.path.join(ASSETS, "imaging_defaults.json")

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus")

# Canonical kinds. Every show (station included) can own all of them now.
KINDS = ["intro", "outro", "bed", "transition", "sting"]

# Folder name per kind. "beds" is kept only as a read alias for the old layout
# so any leftover pinned defaults still resolve; new variants use "bed".
DIRMAP = {"intro": "intro", "outro": "outro", "bed": "bed", "beds": "bed",
          "transition": "transition", "sting": "sting"}


def preset_slugs():
    """Every preset show defined in shows/<slug>.json (sorted)."""
    if not os.path.isdir(SHOWS_DIR):
        return []
    return sorted(fn[:-5] for fn in os.listdir(SHOWS_DIR)
                  if fn.endswith(".json") and not fn.startswith("_"))


def imaging_shows():
    """The shared 'station' show plus every preset — the full imaging roster.

    This is derived from shows/ so it always matches the Studio preset list:
    no hardcoded, duplicated show list anywhere.
    """
    return ["station"] + preset_slugs()


# Kept for backwards-compatible imports; computed once at import time.
IMAGING_SHOWS = imaging_shows()


def norm_kind(kind):
    """Normalize a user/engine kind to its canonical form."""
    k = (kind or "").strip().lower()
    if k == "beds":
        return "bed"
    return k


def asset_dir(show, kind):
    """Absolute folder holding the variants for (show, kind).

    Uniform: assets/<show>/<kind>/ for everyone, station included.
    """
    show = (show or "station").strip() or "station"
    sub = DIRMAP.get(norm_kind(kind), norm_kind(kind))
    return os.path.join(ASSETS, show, sub)


# --------------------------------------------------------------------------- #
#  Pinned defaults
# --------------------------------------------------------------------------- #
def _load_defaults():
    try:
        with open(DEFAULTS_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_defaults(d):
    os.makedirs(os.path.dirname(DEFAULTS_FILE), exist_ok=True)
    with open(DEFAULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _key(show, kind):
    return "%s/%s" % (show, norm_kind(kind))


def get_default(show, kind):
    """Return the pinned default filename for (show, kind), or None."""
    return _load_defaults().get(_key(show, kind))


def set_default(show, kind, filename):
    """Pin a specific variant (by basename) as the default for (show, kind)."""
    filename = os.path.basename(filename)
    if not os.path.exists(os.path.join(asset_dir(show, kind), filename)):
        raise FileNotFoundError(
            "no such variant: %s in %s" % (filename, _rel(asset_dir(show, kind))))
    d = _load_defaults()
    d[_key(show, kind)] = filename
    _save_defaults(d)
    return filename


def clear_default(show, kind):
    """Forget the pinned default so resolution falls back to the latest."""
    d = _load_defaults()
    if d.pop(_key(show, kind), None) is not None:
        _save_defaults(d)
        return True
    return False


# --------------------------------------------------------------------------- #
#  Variants + resolution
# --------------------------------------------------------------------------- #
def _rel(p):
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


def _audio_files(d):
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in os.listdir(d)
            if f.lower().endswith(AUDIO_EXTS) and not f.startswith(".")]


def variants(show, kind):
    """Newest-first list of variant dicts for (show, kind)."""
    show = (show or "station").strip() or "station"
    k = norm_kind(kind)
    files = _audio_files(asset_dir(show, k))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    pinned = get_default(show, k)
    out = []
    for p in files:
        name = os.path.basename(p)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            "name": name,
            "path": p,
            "show": show,
            "kind": k,
            "size": "%.1f MB" % (st.st_size / 1048576.0),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(st.st_mtime)),
            "is_default": (name == pinned),
        })
    return out


def resolve(show, kind):
    """The path the app should actually use for (show, kind): the pinned
    default if any (and still present), otherwise the newest variant."""
    show = (show or "station").strip() or "station"
    k = norm_kind(kind)
    pinned = get_default(show, k)
    if pinned:
        p = os.path.join(asset_dir(show, k), pinned)
        if os.path.exists(p):
            return p
    vs = variants(show, k)
    return vs[0]["path"] if vs else None


def delete_variant(show, kind, filename):
    """Delete one variant file. If it was the pinned default, unpin it."""
    filename = os.path.basename(filename)
    p = os.path.join(asset_dir(show, kind), filename)
    if not os.path.exists(p):
        raise FileNotFoundError(filename)
    os.remove(p)
    if get_default(show, kind) == filename:
        clear_default(show, kind)
    return True


def kinds_for(show):
    """Which kinds make sense for a show. Every show can own all of them now."""
    return list(KINDS)


def overview():
    """Summary of every (show, kind): variant count + what resolves now."""
    rows = []
    for show in imaging_shows():
        for k in kinds_for(show):
            vs = variants(show, k)
            chosen = resolve(show, k)
            pinned = get_default(show, k)
            rows.append({
                "show": show,
                "kind": k,
                "count": len(vs),
                "using": os.path.basename(chosen) if chosen else None,
                "pinned": bool(pinned),
            })
    return rows


# --------------------------------------------------------------------------- #
#  Reset (wipe + rebuild the clean structure)
# --------------------------------------------------------------------------- #
def reset(backup=True):
    """Delete assets/ and rebuild the clean, EMPTY per-show structure.

    A timestamped copy of the current assets/ is moved OUTSIDE the app folder
    (next to it) first, so nothing is ever truly lost. No audio is
    regenerated: every kind folder is created empty (with a .gitkeep) for the
    shared 'station' show plus every preset. Pinned defaults are cleared and a
    jingles/ pool folder is recreated. CREDITS.md (if any) is preserved.
    """
    info = {"backup": None, "shows": 0, "kinds": len(KINDS)}
    if os.path.isdir(ASSETS):
        if backup:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            dest = os.path.abspath(
                os.path.join(ROOT, "..", "assets_backup_" + stamp))
            shutil.move(ASSETS, dest)
            info["backup"] = dest
        else:
            shutil.rmtree(ASSETS)
    os.makedirs(ASSETS, exist_ok=True)
    # Preserve the credits doc if we backed one up.
    if info["backup"]:
        cred = os.path.join(info["backup"], "CREDITS.md")
        if os.path.exists(cred):
            shutil.copy2(cred, os.path.join(ASSETS, "CREDITS.md"))
    # Fresh, empty pinned-defaults map.
    with open(DEFAULTS_FILE, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    # A jingles pool folder (kept by the app).
    jdir = os.path.join(ASSETS, "jingles")
    os.makedirs(jdir, exist_ok=True)
    open(os.path.join(jdir, ".gitkeep"), "w").close()
    # One clean, empty folder per show + kind.
    shows = imaging_shows()
    for s in shows:
        for k in KINDS:
            d = os.path.join(ASSETS, s, k)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, ".gitkeep"), "w").close()
    info["shows"] = len(shows)
    return info


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Bouzid FM imaging asset library")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("shows")

    p = sub.add_parser("list")
    p.add_argument("show")
    p.add_argument("kind")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("resolve")
    p.add_argument("show")
    p.add_argument("kind")

    p = sub.add_parser("set-default")
    p.add_argument("show")
    p.add_argument("kind")
    p.add_argument("filename")

    p = sub.add_parser("clear-default")
    p.add_argument("show")
    p.add_argument("kind")

    p = sub.add_parser("delete")
    p.add_argument("show")
    p.add_argument("kind")
    p.add_argument("filename")

    p = sub.add_parser("overview")
    p.add_argument("--json", action="store_true")

    rp = sub.add_parser("reset", help="wipe assets/ and rebuild the clean, "
                        "empty per-show structure (keeps a backup)")
    rp.add_argument("--no-backup", action="store_true",
                    help="delete outright instead of moving to a backup")

    args = ap.parse_args()

    if args.cmd == "shows":
        print(json.dumps(imaging_shows()))
        return 0
    if args.cmd == "list":
        vs = variants(args.show, args.kind)
        if args.json:
            print(json.dumps(vs, ensure_ascii=False))
            return 0
        if not vs:
            print("(no %s variants for %s)" % (args.kind, args.show))
            return 0
        for v in vs:
            star = " \u2605 default" if v["is_default"] else ""
            print("  %-40s %16s %9s%s"
                  % (v["name"][:40], v["mtime"], v["size"], star))
        return 0
    if args.cmd == "resolve":
        p = resolve(args.show, args.kind)
        print(p or "")
        return 0 if p else 1
    if args.cmd == "set-default":
        try:
            fn = set_default(args.show, args.kind, args.filename)
        except FileNotFoundError as e:
            print("\u2717", e)
            return 1
        print("\u2713 pinned default %s/%s -> %s" % (args.show, args.kind, fn))
        return 0
    if args.cmd == "clear-default":
        if clear_default(args.show, args.kind):
            print("\u2713 cleared default for %s/%s (now uses the latest)"
                  % (args.show, args.kind))
        else:
            print("\u2022 no default was set for %s/%s" % (args.show, args.kind))
        return 0
    if args.cmd == "delete":
        try:
            delete_variant(args.show, args.kind, args.filename)
        except FileNotFoundError:
            print("\u2717 no such variant:", args.filename)
            return 1
        print("\u2713 deleted", args.filename)
        return 0
    if args.cmd == "reset":
        info = reset(backup=not args.no_backup)
        print("\u2713 rebuilt assets/: %d shows \u00d7 %d kinds, clean & empty"
              % (info["shows"], info["kinds"]))
        if info["backup"]:
            print("  backup of the old assets kept at: %s" % info["backup"])
        print("  no audio was regenerated \u2014 use the Imaging tab / "
              "`bouzid imaging` to generate fresh imaging.")
        return 0
    if args.cmd == "overview":
        rows = overview()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False))
            return 0
        for r in rows:
            mark = " (pinned)" if r["pinned"] else ""
            print("  %-14s %-11s %2d variant(s)  using: %s%s"
                  % (r["show"], r["kind"], r["count"],
                     r["using"] or "\u2014", mark))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
