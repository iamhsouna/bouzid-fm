#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bouzid FM — show the music-download progress.

Reads data/fetch-status.json (written live by fetch_trending.py) and prints a
progress bar. Works while the download runs in the background.

Usage:
    python3 fetch_status.py            # one snapshot
    python3 fetch_status.py --watch    # live, refreshes every 2s
    python3 fetch_status.py --log      # also tail the background log
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATUS_FILE = os.path.join(ROOT, "data", "fetch-status.json")
LOG_FILE = os.path.join(ROOT, "data", "initial-fetch.log")


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def bar(frac, width=32):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(width * frac))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {int(frac * 100):3d}%"


def human_age(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def render_once():
    if not os.path.exists(STATUS_FILE):
        print("No download has run yet. Start one with:  bouzid fetch --bg")
        return None
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        print("Status file is being written — try again in a second.")
        return None

    total = s.get("sources_total", 0) or 0
    idx = s.get("source_index", 0) or 0
    frac = (idx / total) if total else 0.0
    alive = (not s.get("finished")) and pid_alive(s.get("pid", -1))

    if s.get("finished"):
        state = "✓ finished" if not s.get("interrupted") else "✕ interrupted"
    elif alive:
        state = "● downloading"
    else:
        state = "⚠ stopped (process not running)"

    added = s.get("tracks_done", 0) - s.get("tracks_at_start", 0)
    print("  Bouzid FM — music download")
    print("  " + bar(1.0 if s.get("finished") else frac))
    print(f"  state    : {state}")
    print(f"  source   : {idx}/{total}   ({s.get('crate','')}: {s.get('label','')})")
    print(f"  tracks   : {s.get('tracks_done', 0)} in library  (+{added} this run)")
    if s.get("failures"):
        print(f"  warnings : {s['failures']} source(s) had errors (skipped)")
    started = s.get("started_at")
    updated = s.get("updated_at")
    if started:
        print(f"  elapsed  : {human_age((updated or time.time()) - started)}")
    return s


def tail(path, n=12):
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        print("\n  --- recent log ---")
        for ln in lines:
            print("  " + ln.rstrip())
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Show music-download progress")
    ap.add_argument("--watch", action="store_true", help="refresh every 2s")
    ap.add_argument("--log", action="store_true", help="also show recent log lines")
    args = ap.parse_args()

    if not args.watch:
        s = render_once()
        if args.log:
            tail(LOG_FILE)
        return

    try:
        while True:
            sys.stdout.write("\033[2J\033[H")  # clear screen
            s = render_once()
            if args.log:
                tail(LOG_FILE)
            if s and (s.get("finished")):
                break
            print("\n  (Ctrl+C to stop watching — the download keeps running)")
            time.sleep(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
