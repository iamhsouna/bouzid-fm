# Project state — Bouzid FM 2.0

**Version:** 1.0.0 (2026-06-15)
**Lineage:** clean rewrite of the 0.2.x app, ported from scratch.
**Provider:** Google Gemini only (text + speech). No other provider exists in
the codebase.

## Design decisions
- **TUI-first.** `scripts/bouzid_tui.py` is the primary interface; the CLI
  (`bin/bouzid`) wraps the same scripts so both behave identically.
- **Navigation** is a logical pipeline: Dashboard → News → Shows → Imaging →
  Music → Settings, with single-key tab switching and a streaming Activity log.
- **Imaging** uses real mastered audio under `assets/<show>/{intro,outro,beds,
  transition}`. No tone/beep synthesis.
- **Jingle builder removed.** No `make_jingles.py`, no `make_assets.py`, no
  jingle assets, no rebuild action.

## What changed vs 0.2.x
- Removed providers: NVIDIA, OpenRouter, ElevenLabs, XTTS, edge-tts — plus all
  their env vars, TUI fields, and the XTTS Docker service / local compose file.
- Removed: jingle builder and synthesized imaging.
- Rewritten Google-only: `make_bulletin.py`, `tts_engine.py`, `show_engine.py`,
  `make_cook.py`, `make_sidibouzid.py`, `build_n8n.py`, `bin/bouzid`,
  `bouzid_tui.py`.
- n8n: a single Google-TTS workflow (`n8n/bouzid-news-google-tts.json`).

## Defaults
- Text model: `gemini-3.5-flash`
- TTS model: `gemini-3.1-flash-tts-preview`
- Voice: `Kore` (female); 30 voices total, grouped by gender.
- Self-throttle: `GOOGLE_TTS_RPM=15`.

## Features carried over
News bulletins, breaking-news flash, Cook with Eya, Story of Sidi Bouzid,
generic preset show engine + presets, AzuraCast upload, weekly scheduling,
n8n automation, Docker stack, trending-music fetch + rescan.

## Layout
```
bin/bouzid            control CLI + TUI launcher
scripts/              TUI + generators + AzuraCast/n8n/mixing helpers
assets/<show>/        mastered imaging (intro/outro/beds/transition)
shows/                preset show JSON
n8n/                  Gemini bulletin workflow
docker-compose.yml    AzuraCast + n8n
```
