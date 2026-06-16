#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — episode library helper (reusable for every show).

One module that manages previously generated episodes of ANY show — news
bulletins, Cook, Sidi Bouzid and every preset show in shows/. The TUI's
per-show library tables and the `bouzid` CLI both go through this code, so the
behaviour is identical everywhere.

Episodes live in per-show folders under MUSIC_DIR:
    news        -> $MUSIC_DIR/news
    cook        -> $MUSIC_DIR/cook
    sidibouzid  -> $MUSIC_DIR/sidibouzid
    <preset>    -> $MUSIC_DIR/<music_subdir|slug>

Actions (all usable from the CLI and the TUI):
    list      pretty/JSON list of episodes for a show
    play      preview locally on the server with ffplay
    air       upload + cut it straight onto the air (interrupt queue)
    delete    remove the local file (and, by default, the AzuraCast copy)
    rename    rename the local file (and, by default, the AzuraCast copy)
    duplicate make a local copy ("<name>_copy.mp3")
    reupload  push the local file into the AzuraCast media library

Usage:
    python3 scripts/library.py list cook
    python3 scripts/library.py air news /path/to/bulletin.mp3
    python3 scripts/library.py rename cook old.mp3 "Tunisian couscous.mp3"
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
sys.path.insert(0, HERE)

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")

# Fixed shows map straight to an AzuraCast upload folder (mirrors the
# generators). Preset shows resolve their folder from the preset file.
# "music" is the general song pool that lives directly in MUSIC_DIR (its
# show folders are sub-directories and are skipped automatically).
FIXED_SUBDIR = {"news": "news", "cook": "cook", "sidibouzid": "sidibouzid",
                "jingles": "jingles", "music": ""}
FIXED_DEST = {"news": "News", "cook": "Cook", "sidibouzid": "SidiBouzid",
              "jingles": "Jingles", "music": "Music"}


# --------------------------------------------------------------------------- #
#  Paths
# --------------------------------------------------------------------------- #
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


def _preset(slug):
    try:
        import show_engine
        return show_engine.load_preset(slug)
    except Exception:  # noqa: BLE001
        return None


def show_dir(slug):
    """Absolute folder that holds a show's generated episodes."""
    sub = FIXED_SUBDIR.get(slug)
    if sub is None:
        preset = _preset(slug)
        sub = (preset or {}).get("music_subdir", slug)
    return os.path.join(music_root(), sub) if sub else music_root()


def dest_folder(slug):
    """AzuraCast media folder this show uploads into."""
    if slug in FIXED_DEST:
        return FIXED_DEST[slug]
    preset = _preset(slug)
    if preset and preset.get("upload_folder"):
        return preset["upload_folder"].rstrip("/")
    return slug[:1].upper() + slug[1:]


def _resolve(slug, f):
    """Accept either an absolute path or a bare filename."""
    if os.path.isabs(f):
        return f
    direct = os.path.join(show_dir(slug), os.path.basename(f))
    return direct


# --------------------------------------------------------------------------- #
#  Listing
# --------------------------------------------------------------------------- #
def _duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=20)
        secs = float((out.stdout or "").strip())
        m, s = divmod(int(round(secs)), 60)
        return "%d:%02d" % (m, s)
    except Exception:  # noqa: BLE001
        return "?"


def _azuracast_basenames():
    """Lowercased basenames already in the AzuraCast library, or None if the
    station can't be reached (so listing still works fully offline)."""
    try:
        import azuracast
        ac = azuracast.AzuraCast()
        names = set()
        for f in ac.files():
            names.add(os.path.basename(f.get("path") or "").lower())
        return names
    except Exception:  # noqa: BLE001
        return None


# Folders under MUSIC_DIR that hold generated show episodes / jingles
# (not music). The general "music" pool lists everything else, recursively.
SHOW_SUBDIRS = {"news", "cook", "sidibouzid", "jingles"}


def show_slugs_with_episodes():
    """Every show slug whose episodes belong in the unified "All shows" view:
    all preset shows (news / cook / sidibouzid are presets too now) plus the
    jingles bucket. The general music pool is intentionally excluded."""
    slugs = []
    try:
        import show_engine
        slugs = list(show_engine.list_shows())
    except Exception:  # noqa: BLE001
        slugs = []
    if "jingles" not in slugs:
        slugs.append("jingles")
    return slugs


def _music_pool_files(root):
    """Every audio file anywhere under MUSIC_DIR (songs live in crate
    sub-folders such as daytime/, night/, ...), skipping the generated show
    folders and any hidden/system directories. Returns absolute paths."""
    skip = {s.lower() for s in SHOW_SUBDIRS}
    found = []
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.relpath(dirpath, root) == ".":
            dirnames[:] = [dn for dn in dirnames
                           if not dn.startswith(".") and dn.lower() not in skip]
        else:
            dirnames[:] = [dn for dn in dirnames if not dn.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(AUDIO_EXTS) and not fn.startswith("."):
                found.append(os.path.join(dirpath, fn))
    return found


def list_episodes(slug, with_duration=True, check_air=True):
    """Return newest-first list of dicts describing a show's episodes.

    The special "music" pool walks MUSIC_DIR recursively (songs live in crate
    sub-folders such as daytime/, night/, ...) and skips the generated show
    folders (news/cook/sidibouzid).
    """
    root = music_root()
    rows = []
    if slug == "all":
        # Aggregate every show + jingles into one newest-first list. Each row
        # keeps its own "slug" so the UI/actions know which library it belongs
        # to. We fetch the AzuraCast on-air set just once here.
        onair = _azuracast_basenames() if check_air else None
        for s in show_slugs_with_episodes():
            for r in list_episodes(s, with_duration=with_duration,
                                   check_air=False):
                if onair is not None:
                    base = os.path.basename(r["path"]).lower()
                    r["on_air"] = "yes" if base in onair else "no"
                rows.append(r)
        rows.sort(key=lambda r: os.path.getmtime(r["path"])
                  if os.path.exists(r["path"]) else 0, reverse=True)
        return rows
    if slug == "music":
        paths = _music_pool_files(root)
    else:
        d = show_dir(slug)
        if not os.path.isdir(d):
            return rows
        paths = [os.path.join(d, fn) for fn in os.listdir(d)
                 if fn.lower().endswith(AUDIO_EXTS) and not fn.startswith(".")]
    onair = _azuracast_basenames() if check_air else None
    paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
               reverse=True)
    for path in paths:
        fn = os.path.basename(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        # For the pool show the crate sub-folder too (e.g. "daytime/song.mp3")
        # so tracks from different crates are distinguishable.
        label = os.path.relpath(path, root) if slug == "music" else fn
        rows.append({
            "name": label,
            "path": path,
            "size": "%.1f MB" % (st.st_size / 1048576.0),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(st.st_mtime)),
            "duration": _duration(path) if with_duration else "",
            "on_air": ("—" if onair is None
                       else ("yes" if fn.lower() in onair else "no")),
            "slug": slug,
        })
    return rows


# --------------------------------------------------------------------------- #
#  Actions
# --------------------------------------------------------------------------- #
def play(slug, path):
    path = _resolve(slug, path)
    if not os.path.exists(path):
        print("✗ file not found:", path)
        return 1
    print("▶ previewing", os.path.basename(path), "(stop with q or Ctrl-C)")
    proc = None
    try:
        proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit",
                                 "-loglevel", "error", path])
        proc.wait()
    except FileNotFoundError:
        print("✗ ffplay is not installed on this machine")
        return 1
    except KeyboardInterrupt:
        print("\n■ stopped")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        return 0
    return 0


def air(slug, path):
    path = _resolve(slug, path)
    if not os.path.exists(path):
        print("✗ file not found:", path)
        return 1
    import azuracast
    ac = azuracast.AzuraCast()
    dest = dest_folder(slug).rstrip("/") + "/" + os.path.basename(path)
    base = os.path.basename(path)
    # Airing must work every time, including for tracks already imported into
    # AzuraCast (every music-pool song was added by the rescan). Upload only if
    # the file is missing, and never abort just because it is already there --
    # reuse the existing copy. This is what fixes "Air now works only once".
    existing = None
    try:
        existing = ac.find_file(dest) or ac.find_file(base)
    except Exception:  # noqa: BLE001
        existing = None
    if existing:
        print("already in library ->", existing.get("path") or dest)
    else:
        print("uploading", base, "->", dest, "...")
        try:
            print(json.dumps(ac.upload(path, dest), ensure_ascii=False)[:200])
        except Exception as e:  # noqa: BLE001
            if ac.find_file(dest) or ac.find_file(base):
                print("  (already present -- reusing the library copy)")
            else:
                print("✗ upload failed:", e)
                return 1
    try:
        # Push+skip synchronously (returns in ~1s once queued). For the inline
        # verdict we prefer the FAST signal: `queued_confirmed` is set the
        # moment our pushed request id leaves the interrupting queue, which
        # means Liquidsoap pulled the track (it airs within a second). That's
        # near-instant, unlike the now-playing feed which lags several seconds.
        # Only if that's inconclusive do we poll now-playing for a short,
        # BOUNDED window (~5s) so the TUI's "Air now" button never hangs on
        # Cancel. The clip is airing regardless once push+skip ran.
        res = ac.play_now(dest, confirm=False)
        print(json.dumps(res, ensure_ascii=False)[:300])
        base_name = res.get("airing") or base
        if res.get("queued_confirmed"):
            print("✓ now ON AIR:", base_name)
        elif ac.wait_until_airing(base_name, timeout=5):
            print("✓ now ON AIR:", base_name)
        else:
            print("✓ cut in on air (now-playing still catching up -- "
                  "check 'bouzid now' if unsure)")
    except Exception as e:  # noqa: BLE001
        print("✗ could not cut it on air:", e)
        print("  (it uploaded fine — press Skip or air it from AzuraCast; "
              "the interrupt path needs an admin API key)")
        return 1
    return 0


def delete(slug, path, sync=True):
    path = _resolve(slug, path)
    base = os.path.basename(path)
    ok = False
    if os.path.exists(path):
        os.remove(path)
        ok = True
        print("✓ deleted local file:", base)
    else:
        print("• local file already gone:", base)
    if sync:
        try:
            import azuracast
            ac = azuracast.AzuraCast()
            f = ac.find_file(base)
            if f and f.get("id") is not None:
                ac.delete_file(f["id"])
                print("✓ removed from AzuraCast library (id %s)" % f["id"])
            else:
                print("• not in the AzuraCast library (nothing to remove)")
        except Exception as e:  # noqa: BLE001
            print("⚠ could not sync the delete to AzuraCast:", e)
    return 0 if ok else 1


def rename(slug, path, newname, sync=True):
    path = _resolve(slug, path)
    if not os.path.exists(path):
        print("✗ file not found:", path)
        return 1
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1]
    newname = os.path.basename(newname.strip())
    if not os.path.splitext(newname)[1]:
        newname += ext
    newpath = os.path.join(os.path.dirname(path), newname)
    if os.path.abspath(newpath) == os.path.abspath(path):
        print("• name unchanged")
        return 0
    if os.path.exists(newpath):
        print("✗ a file named", newname, "already exists")
        return 1
    os.rename(path, newpath)
    print("✓ renamed local:", base, "->", newname)
    if sync:
        try:
            import azuracast
            ac = azuracast.AzuraCast()
            f = ac.find_file(base)
            if f and f.get("id") is not None:
                old_remote = f.get("path") or base
                folder = os.path.dirname(old_remote)
                new_remote = (folder + "/" + newname) if folder else newname
                ac.rename_file(f["id"], new_remote)
                print("✓ renamed in AzuraCast library -> %s" % new_remote)
            else:
                print("• not in the AzuraCast library (nothing to rename)")
        except Exception as e:  # noqa: BLE001
            print("⚠ could not sync the rename to AzuraCast:", e)
    return 0


def duplicate(slug, path):
    path = _resolve(slug, path)
    if not os.path.exists(path):
        print("✗ file not found:", path)
        return 1
    stem, ext = os.path.splitext(os.path.basename(path))
    d = os.path.dirname(path)
    i = 1
    while True:
        suffix = "_copy" if i == 1 else "_copy%d" % i
        cand = os.path.join(d, stem + suffix + ext)
        if not os.path.exists(cand):
            break
        i += 1
    shutil.copy2(path, cand)
    print("✓ duplicated ->", os.path.basename(cand))
    return 0


def reupload(slug, path):
    path = _resolve(slug, path)
    if not os.path.exists(path):
        print("✗ file not found:", path)
        return 1
    import azuracast
    ac = azuracast.AzuraCast()
    dest = dest_folder(slug).rstrip("/") + "/" + os.path.basename(path)
    print("uploading", os.path.basename(path), "->", dest, "...")
    try:
        print(json.dumps(ac.upload(path, dest), ensure_ascii=False)[:200])
        print("✓ uploaded to the AzuraCast library")
    except Exception as e:  # noqa: BLE001
        print("✗ upload failed:", e)
        return 1
    return 0


def empty(slug, sync=True):
    """Delete EVERY file in a library (local + AzuraCast). Destructive.

    Uses the same listing the UI shows, so for the "music" pool it clears
    every track in every crate, and for a show it clears just that show.
    AzuraCast copies are removed too unless sync is False.
    """
    rows = list_episodes(slug, with_duration=False, check_air=False)
    if not rows:
        print("• library is already empty:", show_dir(slug))
        return 0
    ac = None
    if sync:
        try:
            import azuracast
            ac = azuracast.AzuraCast()
        except Exception as e:  # noqa: BLE001
            print("⚠ AzuraCast unreachable, removing local files only:", e)
            ac = None
    n_local = n_remote = 0
    print("— emptying %d file(s) from '%s' —" % (len(rows), slug))
    for r in rows:
        path = r["path"]
        base = os.path.basename(path)
        try:
            if os.path.exists(path):
                os.remove(path)
                n_local += 1
                print("  ✓ local:", r.get("name") or base)
        except OSError as e:
            print("  ✗ could not delete", base, "-", e)
            continue
        if ac is not None:
            try:
                f = ac.find_file(base)
                if f and f.get("id") is not None:
                    ac.delete_file(f["id"])
                    n_remote += 1
            except Exception as e:  # noqa: BLE001
                print("  ⚠ AzuraCast sync failed for", base, "-", e)
    print("✓ removed %d local file(s)%s" % (
        n_local, (", %d from AzuraCast" % n_remote) if sync else ""))
    return 0


def sync(slug, delete_orphans=True):
    """Two-way reconcile a library with AzuraCast so they stay in match:
    upload new local files AND (unless add-only) delete AzuraCast files that no
    longer exist locally. For "all", every show + jingles is reconciled.
    """
    try:
        import azuracast
        ac = azuracast.AzuraCast()
    except Exception as e:  # noqa: BLE001
        print("\u2717 AzuraCast unreachable, cannot sync:", e)
        return 1
    slugs = show_slugs_with_episodes() if slug == "all" else [slug]
    tot = {"uploaded": 0, "deleted": 0, "failed": 0}
    for s in slugs:
        d = show_dir(s)
        dest = dest_folder(s)
        print("\u2192 syncing %s (%s) \u2194 AzuraCast/%s" % (s, d, dest))
        try:
            r = ac.sync_dir(d, dest, delete_orphans=delete_orphans)
        except Exception as e:  # noqa: BLE001
            print("  \u2717 sync failed for %s: %s" % (s, e))
            tot["failed"] += 1
            continue
        for k in ("uploaded", "deleted", "failed"):
            tot[k] += r.get(k, 0)
    print("\u2713 sync done: %d uploaded, %d removed, %d failed across %d "
          "library(ies)." % (tot["uploaded"], tot["deleted"], tot["failed"],
                             len(slugs)))
    return 0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _print_list(slug, as_json, with_duration, check_air):
    rows = list_episodes(slug, with_duration=with_duration, check_air=check_air)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if not rows:
        print("(no episodes yet in %s)" % show_dir(slug))
        return 0
    print("%-44s %7s %10s %16s %5s" %
          ("Episode", "Length", "Size", "Created", "Air"))
    for r in rows:
        print("%-44s %7s %10s %16s %5s" %
              (r["name"][:44], r.get("duration") or "", r["size"],
               r["mtime"], r.get("on_air") or ""))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Bouzid FM episode library helper (works for every show)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list a show's episodes")
    p.add_argument("slug")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-duration", action="store_true")
    p.add_argument("--no-air", action="store_true")

    for verb in ("play", "air", "duplicate", "reupload"):
        q = sub.add_parser(verb)
        q.add_argument("slug")
        q.add_argument("file")

    dl = sub.add_parser("delete")
    dl.add_argument("slug")
    dl.add_argument("file")
    dl.add_argument("--no-sync", action="store_true",
                    help="only delete the local file, leave AzuraCast alone")

    rn = sub.add_parser("rename")
    rn.add_argument("slug")
    rn.add_argument("file")
    rn.add_argument("newname")
    rn.add_argument("--no-sync", action="store_true")

    em = sub.add_parser("empty", help="delete EVERY file in a library")
    em.add_argument("slug")
    em.add_argument("--no-sync", action="store_true",
                    help="only delete local files, leave AzuraCast alone")

    sy = sub.add_parser("sync", help="two-way reconcile a library (or 'all') "
                        "with AzuraCast: upload new, remove deleted")
    sy.add_argument("slug")
    sy.add_argument("--no-delete", action="store_true",
                    help="add-only: upload new files but never delete remote")

    args = ap.parse_args()
    cmd = args.cmd
    if cmd == "list":
        return _print_list(args.slug, args.json, not args.no_duration,
                           not args.no_air)
    if cmd == "play":
        return play(args.slug, args.file)
    if cmd == "air":
        return air(args.slug, args.file)
    if cmd == "duplicate":
        return duplicate(args.slug, args.file)
    if cmd == "reupload":
        return reupload(args.slug, args.file)
    if cmd == "delete":
        return delete(args.slug, args.file, sync=not args.no_sync)
    if cmd == "rename":
        return rename(args.slug, args.file, args.newname,
                      sync=not args.no_sync)
    if cmd == "empty":
        return empty(args.slug, sync=not args.no_sync)
    if cmd == "sync":
        return sync(args.slug, delete_orphans=not args.no_delete)
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
