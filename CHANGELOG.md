# Changelog — Bouzid FM 2.0

All notable changes to the 2.0 line. This is a clean rewrite of Bouzid FM:
Google-only, TUI-first, with the real mastered station imaging.

## 1.3.0 (2026-06-16)

- **Removed n8n entirely.** The n8n workflow was redundant — the Python stack
  already does RSS → Gemini → TTS with voice FX, mixing, and direct AzuraCast
  upload. n8n output was trapped inside the Docker container and never reached
  broadcast. Removed: `n8n/`, `scripts/build_n8n.py`, n8n service from
  `docker-compose.yml`, all env vars and doc references.

- **Unified show commands.** `news`, `cook`, `sidibouzid` are no longer
  standalone CLI commands — use `bouzid show news`, `bouzid show cook`,
  `bouzid show sidibouzid`. All shows share the same flags (`--air`, `--upload`,
  `--play`, `--script-only`, `--no-mix`, `--text`). The TUI already called
  `show_engine.py --show <slug>` directly.

- **Promote jingles to show roles.** New `scripts/use_jingle.py` copies a jingle
  from the pool into `assets/<show>/<role>/` and pins it as the default. TUI
  Jingles tab gains a pool picker, target-show picker, role picker, Use as…
  button, and Refresh pool button. `_jingle_use()` now uses the selected jingle
  (not `--latest`).

- **Help menu rewritten.** Every command now has a real usage example
  (`$ bouzid show news --upload`, `$ bouzid imaging --show cook --kind intro`,
  etc.) and commands are grouped under clear section headers with a 6-step quick
  start at the bottom.

## 1.2.6 (2026-06-16)

- **`bouzid music-reset` now shows live progress — it was never hanging.** The
  previous version printed nothing while it walked your local music and fetched
  AzuraCast's full media list (a normal, but slow, first step on a big library),
  so it looked frozen. It now prints a timestamped line for every phase
  (scanning local, connecting, fetching the media list, each orphan removed as
  `[i/N]`, import, reschedule, restart) and flushes output immediately, so you
  can always see exactly what it is doing.

## 1.2.5 (2026-06-16)

- **Fixed for good: `bouzid` no longer dies with "Permission denied" after a
  pull.** The CLI was installed as a *symlink* to `bin/bouzid`, which only works
  while that file keeps its executable bit — but files come down from git
  non-executable, so every `git pull` that changed `bin/bouzid` stripped the bit
  and broke the command until you re-ran `chmod +x`. The installer now drops a
  tiny *wrapper* at `/usr/local/bin/bouzid` that runs the repo script through
  `bash`, so the launcher never depends on that executable bit again. On an
  existing machine, run once:
  `sudo tee /usr/local/bin/bouzid >/dev/null <<'EOF'` /
  `#!/usr/bin/env bash` / `exec bash "$HOME/bouzid-fm/bin/bouzid" "$@"` / `EOF`
  then `sudo chmod +x /usr/local/bin/bouzid`.

## 1.2.4 (2026-06-16)

- **New: `bouzid music-reset` — make AzuraCast use only your current music.**
  AzuraCast keeps its OWN copy of every imported track (in its Docker media
  storage), so deleting a song from your local library never took it off the
  air — the old copy kept playing and `bouzid rescan` only ever *added*. The new
  command reconciles the two: it removes every track in AzuraCast's music-crate
  folders (morning/daytime/afternoon/evening/night/overnight/friday/weekend)
  that no longer exists in your local MUSIC_DIR, re-imports anything new, then
  re-applies the weekly schedule. News, Jingles and show folders are never
  touched. Run `bouzid music-reset --dry-run` first to preview exactly what
  would be removed.

## 1.2.3 (2026-06-16)

- **Updating is now just `git pull` — you do NOT need to re-run `install.sh`.**
  Two installer fixes stop git from fighting you on every update:
  (1) the installer now sets `git config core.fileMode false` in your checkout,
  so the executable bit it puts on the `bouzid` launcher is no longer seen by
  git as a "local change" that blocks the next pull; and (2) it no longer marks
  the Python scripts executable at all — they are always run via `python3`, so
  there was never a reason to, and doing it was the actual cause of pulls
  aborting with "your local changes would be overwritten" on files you never
  touched. Only the `bouzid` launcher keeps its exec bit now.

## 1.2.2 (2026-06-16)

- **Music fetch no longer skips most of a playlist.** The fetcher used a
  yt-dlp *download archive* to remember every track it had ever downloaded —
  but that archive lives in your music library (which survives reinstalls), so
  after a fresh install it still listed hundreds of old YouTube IDs and skipped
  them with "already recorded in the archive" even though the MP3s were gone.
  The result was lines like "3/45 tracks ok". Now a track is skipped **only when
  its tidy file is actually present on disk**, so fetching always tops a crate
  up to the full playlist and re-downloads anything missing. The obsolete
  `.archive.txt` is no longer used (and is cleaned up when you Clear a category).

## 1.2.1 (2026-06-16)

- **"Air now" works every time now**, not just once. Two fixes: (1) airing no
  longer re-uploads a track that is already in the AzuraCast library (every
  music-pool song is already imported), so it stops bailing out with an upload
  error; and (2) the cut-in has a reliable fallback -- if the interrupting
  queue is still busy with the previous cut-in, the clip is queued as a normal
  request and the track boundary is forced, so a second press (or a different
  song) always goes on air.
- **Tidy, archive-friendly music file names.** Downloads are now named from the
  clean Anghami artist + title (e.g. `Cut Off - Escuro.mp3`,
  `DNDM - Let's Fly.mp3`) instead of the long, messy YouTube video title with
  the `[videoID]` suffix and stray `|` characters. Names are de-noised
  (drops "(Official Video)", "[HD]", bracketed ids), trimmed to a sane length,
  and existing files are never clobbered.

## 1.1.5 (2026-06-16)

- The **app version is now always shown** — in the footer line (bottom-right,
  e.g. “Bouzid FM — v1.1.5”) and in the header subtitle. Easy to confirm at a
  glance which build you're running.
- The header title now reads **Bouzid FM** instead of the class name.

Note: the Reset assets / Sync (Maintenance) controls shipped in 1.1.4 — they
live in the **Settings** tab just below **List voices**. If you didn't see them,
you were running an older copy; the footer version makes the running build
obvious.

## 1.1.4 (2026-06-16)

One-click asset reset and a real two-way AzuraCast sync, so local files and
the on-air library stay in match.

### Reset assets (Settings tab)
- New **Reset assets** button in **Settings → Maintenance**. It wipes the
  whole `assets/` folder and rebuilds the clean, EMPTY per-show structure
  (`intro/outro/bed/transition/sting` for `station` + every preset), clears
  pinned defaults, and recreates the `jingles/` pool. No audio is regenerated
  — use the Imaging tab afterwards.
- A timestamped backup of the old `assets/` is moved next to the app first, so
  nothing is ever lost; `CREDITS.md` is preserved.
- Requires a two-press confirm (warning shown in the log), like Empty library.
- After the rebuild it triggers an **AzuraCast media rescan** automatically.
- CLI: `bouzid reset-assets` (`--no-backup` to skip the backup).

### Two-way AzuraCast sync (“stay in match”)
- New **Sync with AzuraCast** button on the **Library** tab (syncs the
  selected filter) and **Sync media → AzuraCast** in **Settings** (syncs every
  show). Both confirm before running.
- Sync is two-way per show folder: it **uploads new local files** AND
  **removes AzuraCast files that no longer exist locally**, touching only that
  show's folder. Delete/rename/empty already synced; this closes the loop for
  newly added files and full reconciles.
- CLI: `bouzid sync [show|all]` (`--no-delete` for add-only).

### Reusable confirm helper
- The armed two-press confirmation used by Empty library is now generic, so
  Reset, Sync, and Empty all share the same safe “click again to confirm” flow
  and correctly restore their original button labels.

## 1.1.3 (2026-06-16)

Imaging consistency, a clean per-show asset tree, louder/punchier mastering,
and a remaster pass so you can perfect any episode before it airs.

### Imaging Show picker = Studio's list (no duplication)
- The Imaging tab's **Show** picker now uses the **exact same preset list as
  the Studio tab** (`preset_options()`), instead of a separate hardcoded list
  of six shows. There's one source of truth, so the two tabs can never drift
  apart. The imaging roster (`assets_lib.imaging_shows()`) is derived straight
  from the presets plus the shared `station` set.

### Clean, uniform asset tree for every show
- `assets/` was rebuilt from scratch with one tidy, identical layout for
  **every** show (station + all presets):
  `assets/<show>/{intro,outro,bed,transition,sting}/`.
- The old mixed layout (top-level `beds/intro/outro/transition` for the
  station, per-show `beds/` folders, station stings dumped in
  `transition/sting_*.mp3`) is gone. Existing real audio was moved into its
  correct new home; the previous tree is preserved as a backup outside the app.
- `make_imaging.py` is now **fully preset-driven**: every show gets the full
  set (bed, intro, outro, transition + several stings) on uniform paths.
  Well-known shows keep curated prompts; the rest get sensible defaults
  synthesized from their name (override with an optional `imaging` block in
  `shows/<slug>.json`).

### Stings regenerated from scratch, in the right place
- Every show now has its own `assets/<show>/sting/` folder (previously only the
  station had stings, and they lived under `transition/`). The folders start
  empty after the restructure — regenerate them in one command:
  `bouzid imaging --kind sting --replace` (needs a paid Gemini key + network).

### Louder, punchier mastering (beds no longer weak)
- Reworked `audio_mix.py`: the bed sits at a healthy resting level (`bed_gain`
  0.72) with proper sidechain-style ducking under the voice, per-segment
  loudness-matching, and a final limiter. The default output loudness is now
  **−12 LUFS** (punchy) instead of the previous quieter target, so beds and
  the overall mix come through clearly.

### Remaster a generation before airing
- Show generation now saves its voice stems + a mix manifest alongside the
  episode, so you can **re-mix it without re-synthesizing** (fast & free).
- New **Remaster** panel in the Studio tab and a `bouzid remaster <file>`
  command let you tune the mix and preview, then air the version you like:
  `--bed-gain`, `--duck`, `--voice-gain`, `--lufs`, `--intro-gain`,
  `--outro-gain`, `--overlap`, `--fade-in`, `--fade-out`, with `--play` or
  `--air`.

## 1.1.2 (2026-06-16)

A top-to-bottom rework of the control panel around **one job per screen**. The
old workflow had News and Shows as separate tabs even though they do the same
thing, imaging was preview-only, and every show kept its own little library.
That's gone.

### Unified Studio (news is now just a preset)
- The **News** and **Shows** tabs are removed and replaced by a single
  **🎙 Studio** tab. You pick a preset from one dropdown — *news, cook,
  sidibouzid, and every other show* — and Generate, Generate + air, or Preview
  the script. **News is a preset like any other** (it pulls live headlines
  automatically); breaking news is the news preset with custom `--text`.
- New presets `shows/news.json`, `shows/cook.json`, `shows/sidibouzid.json`
  drive the one engine; the standalone `make_cook.py` / `make_sidibouzid.py`
  scripts are deleted. `bouzid news|cook|sidibouzid` now all route through
  `show_engine.py --show <slug>`, so the TUI and CLI behave identically.

### One unified Library
- A single **🗂 Library** tab lists **everything you generate**. A *Filter*
  dropdown switches between *All shows*, each individual show, *Jingles*, and
  the *Music pool* — and the same buttons (play, air, rename, duplicate,
  re-upload, delete, empty) work for every filter. Per-show mini-libraries are
  gone. *All shows* aggregates every show at once.

### One unified Imaging tab + always-latest defaults
- The **🎵 Imaging** tab now **generates** as well as curates. Pick a *Show*
  (or *All shows*) and a *Kind* (or *All kinds*) and generate imaging for every
  show at once, one whole show, one kind across shows, or just one kind of one
  show (e.g. regenerate only the cook intro).
- **Variants instead of overwrites.** Every generation **adds a timestamped
  variant** — it never silently clobbers your imaging. The station
  **automatically uses the newest variant** for each show + kind, *unless you
  pin one as the default you like* (★ Set as default). Clear the default to go
  back to always-latest.
- New `scripts/assets_lib.py` resolves “newest-or-pinned” everywhere
  (`show_engine`, `audio_mix`) and powers the variant table. `make_imaging.py`
  gains `--show`, `--kind`, `--replace`, `--make-default`, and `--voice`. New
  CLI: `bouzid imaging --show … --kind …`, `bouzid assets overview|list|
  resolve|set-default|clear-default`.

### Cleaner navigation
- Tabs and hotkeys are now: `d` Dashboard · `p` Studio · `l` Library ·
  `i` Imaging · `j` Jingles · `m` Music · `g` Settings.

## 1.1.1 (2026-06-16)

### Station imaging regenerator
- **New `bouzid imaging` command** (`scripts/make_imaging.py`). Removes the
  bundled default Pixabay imaging and regenerates the whole set —
  intros / outros / beds / transitions / stings for the station and every show
  — entirely with Google: **Lyria 3** for music beds and stings, **Gemini TTS**
  for the spoken Arabic IDs (mixed under a fresh bed by `audio_mix`).
- Writes to the exact filenames the app already expects, so nothing else
  changes. Old files are backed up to `assets/_backup_*` first (or `--no-backup`
  to delete). Use `--list` to preview, `--dry-run` to rehearse, `--set <slug>`
  to limit, and `--voice <name>` to override the Gemini voice.
- Requires a **paid** Gemini key + network access (Lyria 3 is not on the free
  tier), so run it on your own machine.

## 1.1.0 (2026-06-16)

The Jingle studio gets a real workflow and AI audio engines.

### Jingle studio
- **You confirm before anything is generated.** Voiced jingles are no longer
  written-and-built blindly by Gemini. The new flow is: type an idea, hit
  **✍ Draft script with Gemini**, the Arabic station ID drops into an editable
  review box, you tweak it, *then* you Generate. You can also just type your
  own script and skip Gemini entirely.
- **Three jingle types:**
  - **Voiced** — your script spoken over a music bed (auto-ducked broadcast mix).
  - **Voice only** — the clean spoken script, no bed at all (dry ID).
  - **SFX only** — a sound-effect sting, no voice.
- **Choose your music-bed engine** for voiced jingles: your mastered assets
  (default), **Google Lyria 3**, or **Eleven Music** — with a prompt box to
  describe the bed you want.
- **Choose your SFX engine:** your mastered stings (default) or **ElevenLabs**
  text-to-sound, generated from a prompt.

### Notes
- Lyria 3 music generation needs a **paid** Google Gemini key; if it fails the
  app automatically falls back to your mastered bed.
- New `.env` keys: `LYRIA_MODEL`, `ELEVENLABS_API_KEY`, `ELEVEN_SFX_MODEL`,
  `ELEVEN_MUSIC_MODEL`.

## 1.0.0 (2026-06-15)

First release of the rebuilt app. Ported from the 0.2.x line **from scratch**,
keeping every feature except the jingle builder.

### Highlights
- **Google-only.** Text and speech both run on Google Gemini
  (`gemini-3.5-flash` for writing, `gemini-3.1-flash-tts-preview` for voice).
  Every other provider (NVIDIA, OpenRouter, ElevenLabs, XTTS, edge-tts) and all
  of their settings, fields and Docker services are gone.
- **TUI-first.** `bouzid tui` opens the control panel and that is the primary
  way to run the station.
- **Real mastered imaging.** Intros, outros, music beds and transition stings
  are the produced, loudness-matched audio files — the app no longer synthesizes
  beep tones. Each show has its own imaging under `assets/<show>/`.
- **No jingle builder.** The synthesized-jingle feature was removed entirely
  (no `make_jingles.py`, no jingle assets, no rebuild button).

### Voice
- Default voice: **Kore** (female). 30 Gemini voices available, grouped by
  gender in the Settings tab and the `bouzid voices` command.
- Self-throttle defaults to **15 requests/min** to stay under the free-tier cap.
