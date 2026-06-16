#!/usr/bin/env python3
"""Background scheduler for Bouzid FM — auto-generates shows before air time.

Reads the weekly schedule from config/schedule_data.json and, for each upcoming
slot that has a ``show`` configured, runs ``show_engine.py --show <slug> --air``
if no episode exists yet for that hour.

Usage:
  python3 scripts/scheduler.py                         # foreground loop
  python3 scripts/scheduler.py --once                  # single pass, then exit
  python3 scripts/scheduler.py --interval 10           # check every 10 minutes
  python3 scripts/scheduler.py --lookahead 45          # generate 45 min ahead
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SCHED_JSON = os.path.join(ROOT, "config", "schedule_data.json")
PID_FILE = os.path.join(ROOT, "data", "scheduler.pid")
LOG_FILE = os.path.join(ROOT, "data", "scheduler.log")

_INTERVAL = 5       # minutes between checks
_LOOKAHEAD = 30     # minutes ahead to pre-generate

_WEEK_TEMPLATES = {
    0: "weekday",  # Monday
    1: "weekday",
    2: "weekday",
    3: "weekday",
    4: "friday",
    5: "weekend",
    6: "weekend",
}

_DAY_NAMES_AR = {
    0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء",
    3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد",
}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    try:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _load_schedule():
    try:
        with open(SCHED_JSON, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log("ERROR loading schedule: %s" % exc)
        return None


def _show_folder(slug):
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    return os.path.join(md, slug) if os.path.isabs(md) else os.path.join(ROOT, md, slug)


def _episode_exists(slug, hour):
    """Check if an episode for *slug* was already generated for *hour* today."""
    today = datetime.now().strftime("%Y-%m-%d")
    folder = _show_folder(slug)
    if not os.path.isdir(folder):
        return False
    pattern = "%s_%s_%02d.mp3" % (slug, today, hour)
    return os.path.isfile(os.path.join(folder, pattern))


def _generate(slug):
    """Run show_engine.py --show <slug> --air and return True on success."""
    engine = os.path.join(HERE, "show_engine.py")
    cmd = [sys.executable, engine, "--show", slug, "--upload"]
    log("  spawning: %s --show %s --upload" % (engine, slug))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0:
            log("  done: %s" % slug)
            return True
        log("  ERROR (%d): %s" % (proc.returncode, proc.stderr.strip()[-200:]))
        return False
    except subprocess.TimeoutExpired:
        log("  TIMEOUT generating %s (300s)" % slug)
        return False
    except Exception as exc:  # noqa: BLE001
        log("  ERROR generating %s: %s" % (slug, exc))
        return False


def run_once():
    cfg = _load_schedule()
    if cfg is None:
        return

    now = datetime.now()
    today_iso = now.weekday()  # 0=Monday
    tmpl_name = _WEEK_TEMPLATES.get(today_iso, "weekday")
    templates = cfg.get("templates", {})
    slots = templates.get(tmpl_name, [])
    day_name = _DAY_NAMES_AR.get(today_iso, "")

    if not slots:
        return

    now_min = now.hour * 60 + now.minute
    cutoff = now_min + _LOOKAHEAD
    grace = 15  # minutes past slot start still OK to generate
    daypart_info = cfg.get("dayparts", {})

    pending = []
    for slot in slots:
        show = slot.get("show", "")
        if not show:
            continue
        try:
            start_h, start_m = slot["start"].split(":")
            slot_start = int(start_h) * 60 + int(start_m)
        except Exception:  # noqa: BLE001
            continue
        # Treat overnight slots (e.g. 00:00 at 23:xx) as "next day"
        if slot_start < now_min - grace:
            slot_start += 1440
        # Generate if the slot starts within lookahead (or started within grace)
        if now_min - grace <= slot_start <= cutoff:
            pending.append((slot_start, show, slot))

    if not pending:
        return

    # Sort by start time (earliest first)
    pending.sort()
    log("Schedule: %s (%s) — %d upcoming show(s) in next %d min"
        % (day_name, tmpl_name, len(pending), _LOOKAHEAD))

    for start_min, show, slot in pending:
        hour = start_min // 60
        label = daypart_info.get(slot["daypart"], {}).get("label", slot["daypart"])
        if _episode_exists(show, hour):
            log("  [skip] %s at %s (%s) — episode already exists for hour %02d"
                % (show, slot["start"], label, hour))
            continue
        log("  [gen]  %s at %s (%s)" % (show, slot["start"], label))
        _generate(show)


def run_loop():
    log("Scheduler started — interval=%dmin  lookahead=%dmin"
        % (_INTERVAL, _LOOKAHEAD))
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            log("ERROR in run_once: %s" % exc)
        time.sleep(_INTERVAL * 60)


def main():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Bouzid FM scheduler — auto-generate shows before air time")
    parser.add_argument("--once", action="store_true",
                        help="Single pass, then exit")
    parser.add_argument("--interval", type=int, default=5,
                        help="Check interval in minutes (default: 5)")
    parser.add_argument("--lookahead", type=int, default=30,
                        help="Minutes ahead to pre-generate (default: 30)")
    args = parser.parse_args()

    # Patch module-level constants so run_loop picks them up
    import sys  # noqa: PLC0415
    mod = sys.modules[__name__]
    mod._INTERVAL = args.interval
    mod._LOOKAHEAD = args.lookahead

    if args.once:
        run_once()
    else:
        run_loop()


if __name__ == "__main__":
    main()
