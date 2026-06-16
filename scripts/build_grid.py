#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bouzid FM — weekly program grid exporter.

Reads config/schedule_config.py (the single source of truth for the weekly
program) and writes a human-readable grid to weekly_grid.md and weekly_grid.csv.

This is presentation only — it never talks to the radio server. To actually
push the playlists/dayparts into AzuraCast, use:  bouzid schedule

Stdlib only.

    python3 scripts/build_grid.py        # writes weekly_grid.md + weekly_grid.csv
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_CFG_PATH = os.path.join(ROOT, "config", "schedule_config.py")
_spec = importlib.util.spec_from_file_location("schedule_config", _CFG_PATH)
cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfg)


def hhmm_to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def build_blocks():
    blocks = []
    for wd, (ar, en, template) in cfg.WEEK.items():
        for start, end, daypart in template:
            dp = cfg.DAYPARTS[daypart]
            pl = cfg.PLAYLISTS[dp["playlist"]]
            end_min = 24 * 60 if end == "24:00" else hhmm_to_min(end)
            blocks.append({
                "weekday": wd, "day_ar": ar, "day_en": en,
                "start": start, "end": end,
                "dur_min": end_min - hhmm_to_min(start),
                "daypart": daypart, "daypart_label": dp["label"],
                "playlist_key": dp["playlist"], "playlist_label": pl["label"],
                "news_every": dp["news_every"], "jingle": dp["jingle"],
            })
    return blocks


def export_grid(blocks):
    csv = ["Day,Start,End,Daypart,Playlist,News every (min),Jingle"]
    for b in blocks:
        csv.append(f"{b['day_en']},{b['start']},{b['end']},{b['daypart_label']},"
                   f"{b['playlist_label']},{b['news_every'] or 'none'},"
                   f"{'yes' if b['jingle'] else 'no'}")
    with open(os.path.join(ROOT, "weekly_grid.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(csv) + "\n")
    md = [f"# {cfg.STATION_NAME} — Weekly Program Grid", ""]
    for wd, (ar, en, _t) in cfg.WEEK.items():
        md += [f"## {en} / {ar}", "",
               "| Time | Segment | Playlist | News | Jingle |",
               "|------|---------|----------|------|--------|"]
        for b in [x for x in blocks if x["weekday"] == wd]:
            news = "—" if not b["news_every"] else f"every {b['news_every']}m"
            jin = "yes" if b["jingle"] else "—"
            md.append(f"| {b['start']}–{b['end']} | {b['daypart_label']} | "
                      f"{b['playlist_label']} | {news} | {jin} |")
        md.append("")
    with open(os.path.join(ROOT, "weekly_grid.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("Wrote weekly_grid.md and weekly_grid.csv")


def main():
    export_grid(build_blocks())


if __name__ == "__main__":
    main()
