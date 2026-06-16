#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — weekly programming grid (single source of truth).

This file now loads from config/schedule_data.json so the TUI Schedule tab
can read and write the schedule without touching Python code.
"""
import json
import os
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_HERE, "schedule_data.json")
with open(_JSON_PATH, encoding="utf-8") as _f:
    _DATA = json.load(_f)

STATION_NAME = _DATA.get("station_name", "Bouzid FM")
TIMEZONE = _DATA.get("timezone", "Africa/Tunis")
JINGLE_EVERY_SONGS = _DATA.get("jingle_every_songs", 4)
JINGLE_FOLDER = _DATA.get("jingle_folder", "jingles")
NEWS_FOLDER = _DATA.get("news_folder", "news")

PLAYLISTS = {}
for _k, _v in _DATA.get("playlists", {}).items():
    PLAYLISTS[_k] = dict(_v)

DAYPARTS = {}
for _k, _v in _DATA.get("dayparts", {}).items():
    if _v is not None:
        DAYPARTS[_k] = dict(_v)

_TEMPLATES = _DATA.get("templates", {})
WEEKDAY_TEMPLATE = [(s["start"], s["end"], s["daypart"]) for s in _TEMPLATES.get("weekday", [])]
FRIDAY_TEMPLATE = [(s["start"], s["end"], s["daypart"]) for s in _TEMPLATES.get("friday", [])]
WEEKEND_TEMPLATE = [(s["start"], s["end"], s["daypart"]) for s in _TEMPLATES.get("weekend", [])]

_TEMPLATE_LOOKUP = {
    "weekday": WEEKDAY_TEMPLATE,
    "friday": FRIDAY_TEMPLATE,
    "weekend": WEEKEND_TEMPLATE,
}
WEEK = OrderedDict()
_WEEK_DATA = _DATA.get("week", {})
for _day_key in sorted(_WEEK_DATA, key=lambda k: int(k)):
    _entry = _WEEK_DATA[_day_key]
    _tmpl_name = _entry["template"]
    _tmpl = _TEMPLATE_LOOKUP.get(_tmpl_name, WEEKDAY_TEMPLATE)
    WEEK[int(_day_key)] = (_entry["ar"], _entry["en"], _tmpl)

# Show schedule: show_slug -> [(day_iso, start_hhmm, end_hhmm)]
SHOWS = OrderedDict()
for _day_key in sorted(_WEEK_DATA, key=lambda k: int(k)):
    _entry = _WEEK_DATA[_day_key]
    _tmpl_name = _entry["template"]
    for _slot in _TEMPLATES.get(_tmpl_name, []):
        _show = _slot.get("show")
        if _show:
            SHOWS.setdefault(_show, []).append({
                "day": int(_day_key),
                "start": _slot["start"],
                "end": _slot["end"],
            })
