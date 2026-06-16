#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — promote a generated jingle into a show's imaging role.

Jingles land in a flat pool (assets/jingles/) with no per-show or per-role
binding, so the scheduler rotates them as "one every N songs" around the clock.
Imaging assets live in assets/<show>/<kind>/ (intro/outro/bed/transition/sting)
with a pinned default per (show, kind) that the show engine reads at build time.

This bridges the two: it copies a jingle you like into
    assets/<show>/<kind>/
and pins it as the default for that role via assets_lib.set_default(). The next
time the show is built (show_engine.asset()), the promoted jingle is used
automatically — no engine or scheduler changes.

Usage:
    use_jingle.py <jingle> --for <show> --as <role>
    use_jingle.py <jingle> --for cook --as intro
    use_jingle.py <jingle> --for station --as bed --move
    use_jingle.py --list
    use_jingle.py --for cook --as intro --latest   # newest jingle in the pool
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import assets_lib  # noqa: E402

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


def jingles_dir():
    """The flat jingle pool (matches make_jingle.jingles_dir)."""
    md = _env("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    return os.path.join(md, "jingles")


def newest_jingle():
    """Newest audio file in the jingle pool, or None if empty."""
    d = jingles_dir()
    if not os.path.isdir(d):
        return None
    best = None
    for base, _dirs, names in os.walk(d):
        for n in names:
            if n.lower().endswith(AUDIO_EXTS) and not n.startswith("."):
                p = os.path.join(base, n)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


def _resolve_source(jingle):
    """Accept an absolute path, a repo-relative path, or a bare filename
    looked up in the jingle pool."""
    if not jingle:
        return None
    if os.path.isabs(jingle) and os.path.exists(jingle):
        return jingle
    rel = os.path.join(ROOT, jingle)
    if os.path.exists(rel):
        return rel
    # Bare filename -> look in the pool (and the assets/jingles mirror).
    base = os.path.basename(jingle)
    for cand in (os.path.join(jingles_dir(), base),
                 os.path.join(ASSETS_JINGLES, base)):
        if os.path.exists(cand):
            return cand
    return None


ASSETS_JINGLES = os.path.join(assets_lib.ASSETS, "jingles")


def promote(jingle, show, kind, move=False):
    """Copy (or move) `jingle` into assets/<show>/<kind>/ and pin it as the
    default for that role. Returns the destination basename.

    Raises FileNotFoundError if the source is missing, or ValueError if the
    show/kind is unknown.
    """
    kind = assets_lib.norm_kind(kind)
    if kind not in assets_lib.KINDS:
        raise ValueError(
            "unknown role '%s' — must be one of %s"
            % (kind, ", ".join(assets_lib.KINDS)))
    known = set(assets_lib.imaging_shows())
    if show not in known:
        raise ValueError(
            "unknown show '%s' — known shows: %s"
            % (show, ", ".join(sorted(known))))
    src = _resolve_source(jingle)
    if not src:
        raise FileNotFoundError(
            "jingle not found: %s (looked as absolute, repo-relative, and in "
            "the jingle pool at %s)" % (jingle, jingles_dir()))

    dest_dir = assets_lib.asset_dir(show, kind)
    os.makedirs(dest_dir, exist_ok=True)

    # Disambiguate on a name collision so we never overwrite an existing
    # variant silently. Suffix the role before the extension.
    base = os.path.basename(src)
    dest = os.path.join(dest_dir, base)
    if os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(src):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(dest_dir, "%s_%s%s" % (stem, kind, ext))

    if os.path.abspath(dest) == os.path.abspath(src):
        # Source already lives in the destination; just pin it.
        pass
    elif move:
        shutil.move(src, dest)
    else:
        shutil.copy2(src, dest)

    pinned = assets_lib.set_default(show, kind, os.path.basename(dest))
    verb = "moved" if move else "copied"
    print("✓ %s %s -> %s" % (verb, os.path.basename(src),
                             os.path.relpath(dest, ROOT)))
    print("✓ '%s' is now the %s for '%s' (pinned as default)"
          % (pinned, kind, show))
    print("  the next time you build the %s show, this jingle plays that role "
          "automatically." % show)
    return pinned


def list_bindings():
    """Print the current (show, role) -> default-file bindings."""
    defaults = assets_lib._load_defaults()
    if not defaults:
        print("(no jingles/imaging pinned as defaults yet)")
        print("promote one with: use-jingle <file> --for <show> --as <role>")
        return 0
    print("%-24s %-12s %s" % ("show", "role", "default file"))
    print("-" * 64)
    for key in sorted(defaults):
        show, _, role = key.partition("/")
        fname = defaults[key]
        here = os.path.exists(os.path.join(assets_lib.asset_dir(show, role),
                                           fname))
        mark = " " if here else "✗ (missing)"
        print("%-24s %-12s %s %s" % (show, role, fname, mark))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Promote a generated jingle into a show's imaging role "
                    "(intro/outro/bed/transition/sting).")
    ap.add_argument("jingle", nargs="?",
                    help="the jingle file (absolute, repo-relative, or a bare "
                         "name from the pool)")
    ap.add_argument("--for", dest="show", default="station",
                    help="target show slug (default: station)")
    ap.add_argument("--as", dest="kind", default="intro",
                    help="role: one of %s (default: intro)"
                         % ", ".join(assets_lib.KINDS))
    ap.add_argument("--move", action="store_true",
                    help="move instead of copy (removes it from the jingle "
                         "pool)")
    ap.add_argument("--latest", action="store_true",
                    help="use the newest jingle in the pool (ignore <jingle>)")
    ap.add_argument("--list", action="store_true",
                    help="list current (show, role) -> default bindings")
    args = ap.parse_args()

    if args.list:
        return list_bindings()

    if args.latest:
        src = newest_jingle()
        if not src:
            sys.exit("no jingles found in the pool at %s" % jingles_dir())
        args.jingle = src

    if not args.jingle:
        ap.error("provide a jingle file, or use --latest / --list")

    try:
        promote(args.jingle, args.show, args.kind, move=args.move)
    except (FileNotFoundError, ValueError) as e:
        sys.exit("✗ %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
