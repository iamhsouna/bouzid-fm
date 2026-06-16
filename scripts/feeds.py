#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — optional live-data feeds for shows (weather, prayer times).

Shows that want real numbers (the farmer's brief, prayer times, Ramadan
companion…) set "feed": "weather" or "feed": "prayer" in their preset. The
show engine then injects the returned Arabic sentence into the LLM prompt.

Every network call is best-effort with a short timeout. On ANY failure we
return "" so the show still airs from the LLM / built-in fallback alone — the
station never goes silent because an API was down.

Free, no API key required:
  - weather: Open-Meteo (https://open-meteo.com)
  - prayer : Aladhan      (https://aladhan.com)

Coordinates default to Sidi Bouzid, Tunisia; override with
BOUZID_LAT / BOUZID_LON / BOUZID_CITY / BOUZID_COUNTRY in .env.
"""
import json
import os
import urllib.parse
import urllib.request

LAT = float(os.environ.get("BOUZID_LAT", "35.0382"))
LON = float(os.environ.get("BOUZID_LON", "9.4858"))
CITY = os.environ.get("BOUZID_CITY", "Sidi Bouzid")
COUNTRY = os.environ.get("BOUZID_COUNTRY", "Tunisia")
TZ = os.environ.get("TZ", "Africa/Tunis")
TIMEOUT = float(os.environ.get("BOUZID_FEED_TIMEOUT", "6"))


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BouzidFM/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


# Open-Meteo weather codes -> short Tunisian-Arabic descriptions.
WEATHER_AR = {
    0: "سما صافية", 1: "صحو غالباً", 2: "غيوم متفرقة",
    3: "الجو غائم", 45: "ضباب", 48: "ضباب كثيف",
    51: "رذاذ خفيف", 53: "رذاذ", 55: "رذاذ كثيف",
    61: "مطر خفيف", 63: "مطر", 65: "مطر غزير",
    71: "ثلج خفيف", 73: "ثلج", 75: "ثلج كثيف",
    80: "زخات مطر", 81: "زخات", 82: "زخات قوية",
    95: "رعد", 96: "رعد وبَرَد", 99: "رعد وبَرَد قوي",
}


def weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=%s&longitude=%s"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,weathercode"
        "&timezone=%s&forecast_days=1"
        % (LAT, LON, urllib.parse.quote(TZ))
    )
    d = _get_json(url)
    dd = d["daily"]
    tmax = round(dd["temperature_2m_max"][0])
    tmin = round(dd["temperature_2m_min"][0])
    pop = dd["precipitation_probability_max"][0]
    desc = WEATHER_AR.get(dd["weathercode"][0], "")
    return (
        "طقس %s اليوم: %s، الحرارة بين %s و %s درجة، "
        "واحتمال نزول المطر %s بالمائة."
        % (CITY, desc, tmin, tmax, pop)
    )


def _t(timings, key):
    # Aladhan returns e.g. "05:12 (CET)" -> keep "05:12".
    return str(timings.get(key, "")).split(" ")[0]


def prayer():
    url = (
        "https://api.aladhan.com/v1/timingsByCity"
        "?city=%s&country=%s&method=18"
        % (urllib.parse.quote(CITY), urllib.parse.quote(COUNTRY))
    )
    d = _get_json(url)
    t = d["data"]["timings"]
    return (
        "مواقيت الصلاة اليوم في %s: الفجر %s، الظهر %s، "
        "العصر %s، المغرب %s، العشاء %s."
        % (CITY, _t(t, "Fajr"), _t(t, "Dhuhr"), _t(t, "Asr"),
           _t(t, "Maghrib"), _t(t, "Isha"))
    )


_FEEDS = {"weather": weather, "prayer": prayer}


def get(kind):
    """Return a short Arabic data sentence for the feed, or "" on any failure."""
    fn = _FEEDS.get((kind or "").strip().lower())
    if not fn:
        return ""
    try:
        return fn() or ""
    except Exception:
        return ""


if __name__ == "__main__":
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "weather"
    print(get(which) or "(feed unavailable)")
