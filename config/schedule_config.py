#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — weekly programming grid (single source of truth).

The scheduler reads this and pushes shows + recurrence + autoloading playlists
into AzuraCast. Rules baked in:
  * News at the top of every hour (cadence configurable per daypart).
  * Music auto-fills the rest of each hour from that daypart's playlist.
  * A short station jingle plays right before each bulletin.
  * Friday + weekend evenings get special Tunisian themes.
Crate folder names here MUST match the crate keys in config/crates.json.
"""

PLAYLISTS = {
    "calm_morning":   {"label": "صباح هادئ (Calm / Jazz / Pop)",  "folder": "morning"},
    "daytime_mix":    {"label": "خليط النهار (Daytime Mix)",       "folder": "daytime"},
    "shaabi_malouf":  {"label": "شعبي ومالوف (Shaabi / Malouf)",   "folder": "afternoon"},
    "tarab_khaleeji": {"label": "طرب وخليجي (Tarab / Khaleeji)",   "folder": "evening"},
    "tunisian_best":  {"label": "أجمل التونسي (Best of Tunisian)","folder": "night"},
    "ambient_west":   {"label": "ليل هادئ (Ambient / Chill)",       "folder": "overnight"},
    "friday_classic": {"label": "كلاسيك الجمعة (Classic Malouf)","folder": "friday"},
    "weekend_party":  {"label": "سهرة الويكاند (Weekend Party)",   "folder": "weekend"},
}

JINGLE_FOLDER = "jingles"
NEWS_FOLDER = "news"

# A voiced station ID (jingle) is dropped in after every this-many songs, all
# day long. Lower = more frequent. Generate the jingle audio with:
#   bouzid jingles      (uses Google Gemini TTS; needs GEMINI_API_KEY in .env)
JINGLE_EVERY_SONGS = 4

DAYPARTS = {
    "overnight": {"label": "ليل بوزيد",        "playlist": "ambient_west",   "news_every": 120, "jingle": False},
    "morning":   {"label": "صباح الخير تونس", "playlist": "calm_morning",  "news_every": 60,  "jingle": True},
    "midday":    {"label": "وسط النهار",       "playlist": "daytime_mix",   "news_every": 60,  "jingle": True},
    "noon_bull": {"label": "نشرة الظهر",       "playlist": "daytime_mix",   "news_every": 30,  "jingle": True},
    "afternoon": {"label": "بعد الظهر",        "playlist": "shaabi_malouf", "news_every": 60,  "jingle": True},
    "evening":   {"label": "المساء",           "playlist": "tarab_khaleeji","news_every": 60,  "jingle": True},
    "night":     {"label": "ليلة تونسية",      "playlist": "tunisian_best", "news_every": 90,  "jingle": False},
    "fri_eve":   {"label": "سهرة الجمعة",      "playlist": "friday_classic","news_every": 90,  "jingle": True},
    "wknd_eve":  {"label": "سهرة الويكاند",   "playlist": "weekend_party", "news_every": 90,  "jingle": True},
}

WEEKDAY_TEMPLATE = [
    ("00:00", "06:00", "overnight"),
    ("06:00", "09:00", "morning"),
    ("09:00", "12:00", "midday"),
    ("12:00", "13:00", "noon_bull"),
    ("13:00", "18:00", "afternoon"),
    ("18:00", "20:00", "evening"),
    ("20:00", "24:00", "night"),
]
FRIDAY_TEMPLATE = [
    ("00:00", "06:00", "overnight"),
    ("06:00", "09:00", "morning"),
    ("09:00", "12:00", "midday"),
    ("12:00", "13:30", "noon_bull"),
    ("13:30", "18:00", "afternoon"),
    ("18:00", "21:00", "fri_eve"),
    ("21:00", "24:00", "night"),
]
WEEKEND_TEMPLATE = [
    ("00:00", "07:00", "overnight"),
    ("07:00", "10:00", "morning"),
    ("10:00", "13:00", "midday"),
    ("13:00", "14:00", "noon_bull"),
    ("14:00", "19:00", "afternoon"),
    ("19:00", "23:00", "wknd_eve"),
    ("23:00", "24:00", "night"),
]

WEEK = {
    0: ("الإثنين",   "Monday",    WEEKDAY_TEMPLATE),
    1: ("الثلاثاء", "Tuesday",   WEEKDAY_TEMPLATE),
    2: ("الأربعاء", "Wednesday", WEEKDAY_TEMPLATE),
    3: ("الخميس",   "Thursday",  WEEKDAY_TEMPLATE),
    4: ("الجمعة",   "Friday",    FRIDAY_TEMPLATE),
    5: ("السبت",    "Saturday",  WEEKEND_TEMPLATE),
    6: ("الأحد",    "Sunday",    WEEKEND_TEMPLATE),
}

STATION_NAME = "Bouzid FM"
TIMEZONE = "Africa/Tunis"
