# Bouzid FM 2.0

A self-hosted Tunisian AI radio station. **Google-only, TUI-first.**

Bouzid FM writes its own Arabic content with Google Gemini, voices it with
Gemini TTS, mixes it under real mastered station imaging, and broadcasts it
through AzuraCast — all from one clean terminal control panel.

> 2.0 is a clean rewrite of the 0.2.x app. It keeps every feature **except the
> jingle builder**, drops every non-Google provider, and replaces synthesized
> beep imaging with produced, loudness-matched audio.

---

## What it does

- **Auto-scheduler** — the auto-scheduler (`bouzid scheduler`) polls every 5 min and
  generates shows before their air time. Runs as a background daemon.
- **AI news** — pulls live Tunisian RSS, writes a Fus-ha bulletin with Gemini,
  voices it, mixes intro/bed/outro, and can air it automatically.
- **Breaking news** — one command writes an urgent flash and puts it on air now.
- **Cook with Eya** (كوك مع إيّة) — a full daily Tunisian cooking show.
- **Story of Sidi Bouzid** (حكاية سيدي بوزيد) — a ~3-minute daily culture &
  history segment about the town and region.
- **Preset shows** — a generic engine that produces any show defined in
  `shows/*.json`.
- **Music** — fetches trending Tunisian tracks and schedules a full week of
  programming in AzuraCast.
- **Automation** — a cron job can produce bulletins on a timer (`bouzid news --upload`).

## Requirements

- Linux with Docker + Docker Compose
- Python 3.10+
- `ffmpeg` / `ffprobe` on the PATH
- A free **Google Gemini API key** — https://aistudio.google.com/apikey

## Install

```bash
unzip bouzid-fm-1.0.0.zip
cd bouzid-fm-2.0
cp .env.example .env        # then edit .env and paste your GEMINI_API_KEY
./install.sh                # builds the venv, starts the stack, installs `bouzid`
```

## Run it (the TUI is the app)

```bash
bouzid tui
```

The panel has one logical flow, with a single screen for each job. **There is
no separate "News" or "Shows" tab any more** — every show (news included) is a
preset you pick in Studio; every generated file lives in one Library you filter;
all imaging is generated and curated from one Imaging tab.

Navigation (one key jumps to each tab, `s` is the Schedule tab):

| Key | Tab        | What's there                                                       |
|-----|------------|-------------------------------------------------------------------|
| `d` | Dashboard  | Start/stop the stack, health, now-playing, skip, URLs, scheduler start/stop/status |
| `p` | Studio     | **One control for every show.** Pick a preset (news, cook, sidibouzid, or any show), optionally type custom text, then Generate / Air / Preview script. News is just a preset that pulls live headlines. |
| `s` | Schedule   | Visual schedule editor: template selector, editable time slots with start/end/daypart/show pickers, daypart properties, preset loader, Save & Apply to AzuraCast |
| `l` | Library    | **One browser for everything you generate.** Filter by show (or *All shows*, Jingles, Music pool); the same buttons — play, air, rename, duplicate, re-upload, delete — work for every filter. |
| `i` | Imaging    | **One screen for all imaging.** The *Show* picker is the **same preset list as the Studio tab** (no separate list to drift). Pick a preset and a kind (or all kinds) and generate intros / outros / beds / transitions / stings. Each run adds a new variant; manage variants and pin a default below. Every show has its own **curated production-grade prompts** (key, BPM, instrumentation, arrangement) for professional, show-specific results. |
| `j` | Jingles    | Draft-then-confirm jingle studio (voiced / voice-only / SFX)      |
| `m` | Music      | Fetch trending music, rescan, apply the weekly schedule           |
| `g` | Settings   | Gemini key, voice, models, throttle                               |

**Always-latest, opt-in defaults.** The station automatically uses the *newest*
imaging variant for each show + kind. If there's one you specifically like, pin
it as the default in the Imaging tab (★ Set as default) and it sticks until you
clear it.

The Activity log at the bottom streams the output of everything you run.
Press `Ctrl+L` to clear it, `q` to quit.

## Command line (optional)

Everything in the TUI is also a command — the TUI and CLI call the same scripts:

```bash
bouzid start            # start the stack
bouzid shows            # list every preset (news, cook, sidibouzid, + all shows)
bouzid news --upload    # news is just a preset; generate a bulletin and air it
bouzid breaking         # air an urgent flash from the latest headlines
bouzid show cook --air  # any preset by slug (cook, sidibouzid, farmer, …)
bouzid show news --text "عاجل: …" --air   # speak custom text on a preset
bouzid sync cook        # two-way sync one show with AzuraCast (upload new, remove deleted)
bouzid sync all         # reconcile every show's library with AzuraCast
bouzid reset-assets     # wipe assets/ & rebuild the clean structure, then rescan
bouzid voices           # list the Gemini voices
bouzid schedule         # apply the weekly grid
bouzid imaging                         # generate a new variant for every show/kind
bouzid imaging --show cook --kind intro   # just the cook intro
bouzid imaging --kind sting --replace     # regenerate ALL stings from scratch
bouzid imaging --show station --kind bed --replace   # regen, drop old in scope
bouzid scheduler start    # start auto-show generation daemon
bouzid scheduler status   # check if it's running
bouzid scheduler once     # one generation pass, then exit
bouzid remaster <file.mp3> --bed-gain 0.8 --lufs -12 --play   # perfect the mix
bouzid remaster <file.mp3> --duck 0.5 --air                   # then air it
bouzid assets overview                 # see variants + which default is pinned
bouzid assets set-default cook intro cook_intro_2026….mp3   # pin a favourite
bouzid library list all                # every generated episode, all shows
bouzid health           # check the stack
```

Run `bouzid help` for the full list.

## Configuration (.env)

The important keys (see `.env.example` for everything):

| Key                 | Default                          | Meaning                          |
|---------------------|----------------------------------|----------------------------------|
| `GEMINI_API_KEY`    | *(required)*                     | Your Google Gemini key           |
| `GEMINI_TEXT_MODEL` | `gemini-3.5-flash`               | Model used to write content      |
| `GEMINI_TTS_MODEL`  | `gemini-3.1-flash-tts-preview`   | Model used for speech            |
| `GEMINI_TTS_VOICE`  | `Kore`                           | Station voice (female by default)|
| `GOOGLE_TTS_RPM`    | `15`                             | Self-throttle, requests / minute |
| `MUSIC_DIR`         | `$HOME/bouzid-music`             | Music library (kept on reinstall)|

## Project layout

```
bin/bouzid            the control CLI (and `bouzid tui` launcher)
scripts/              all the Python (TUI, generators, AzuraCast, mixing)
assets/<show>/        mastered imaging, one folder per show + kind:
                      intro/ outro/ bed/ transition/ sting/
shows/                preset show definitions (*.json)
docker-compose.yml    the AzuraCast stack
```

See `TUTORIAL.md` for a guided first run and `TESTING.md` for a full checklist.
