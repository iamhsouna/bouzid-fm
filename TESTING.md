# Bouzid FM 2.0 — how to test this release

Google-only build. Run these in order; each step says what “good” looks like.
Replace `YOUR_GEMINI_KEY` with your real key from
https://aistudio.google.com/apikey . You do **not** need any other provider key.

All commands assume you're inside the unzipped `bouzid-fm-2.0/` folder.

---

## 0. Unpack & configure

```bash
unzip bouzid-fm-1.0.0.zip
cd bouzid-fm-2.0
cp .env.example .env
sed -i 's/^GEMINI_API_KEY=.*/GEMINI_API_KEY=YOUR_GEMINI_KEY/' .env
```

**Good:** `.env` exists and contains your key. Check with
`grep GEMINI_API_KEY .env`.

## 1. Sanity-check the code (no stack needed)

```bash
# every Python script compiles
python3 -m py_compile scripts/*.py && echo "PY OK"
# the shell scripts parse
bash -n bin/bouzid install.sh && echo "SH OK"
# prove there are NO non-Google providers and NO jingle BUILDER left
! grep -rinE 'nvidia|openrouter|elevenlabs|\beleven\b|xtts|edge-tts|edge_tts|make_jingles|make_assets' \
    scripts bin *.sh .env .env.example 2>/dev/null && echo "CLEAN: google-only, builder removed"
```

**Good:** you see `PY OK`, `SH OK`, and `CLEAN: google-only, builder removed`.

> Note: the word “jingle” still appears in the scheduler (`azuracast.py`,
> `build_grid.py`). That's the normal AzuraCast *station-ID* feature — a kept
> scheduling option, **not** the removed synthesized-jingle builder. The beep
> generator (`make_jingles.py` / `make_assets.py`) is gone for good.
> The only Google voice names you'll ever see (e.g. `Kore`, `Leda`) are fine.

## 2. Voice test (this exercises your Gemini key)

```bash
bouzid voices                       # lists 30 voices, grouped F / M
bouzid say "مرحبا بكم في إذاعة بوزيد إف إم"   # writes /tmp/bouzid_tts.mp3
ffprobe -hide_banner /tmp/bouzid_tts.mp3   # confirm it's real audio
```

**Good:** the voice list prints, and `/tmp/bouzid_tts.mp3` exists and ffprobe
shows a duration. If you get a key error, re-check step 0.

## 3. Start the stack

```bash
bouzid start
bouzid health        # repeat until AzuraCast + n8n both say OK (first boot ~2 min)
bouzid urls          # prints the AzuraCast / n8n / stream URLs
```

**Good:** `bouzid health` reports `OK AzuraCast` and `OK n8n`.

## 4. The TUI (the main way to use it)

```bash
bouzid tui
```

**Good:** the panel opens on **Dashboard**. Test the new navigation:
- Press `d` `n` `s` `i` `m` `g` — each jumps straight to a tab.
- On **Settings** (`g`): press **Test voice**, watch the Activity log write
  `/tmp/bouzid_voice_test.mp3`.
- On **Imaging** (`i`): pick `station` + `intro`, press **Preview** — you should
  hear a real mastered sting, **not** a beep.
- Press `q` to quit.

## 5. Generate a news bulletin

```bash
bouzid news --script-only     # just the written script (fast, cheap)
bouzid news                   # full: write + voice + mix -> prints the mp3 path
ffprobe -hide_banner "$(ls -t /tmp/bouzid_*news*.mp3 2>/dev/null | head -1)"
```

**Good:** `--script-only` prints Arabic text; the full run prints a path to a
mixed mp3 that ffprobe can read.

## 6. The signature shows

```bash
bouzid cook --script-only         # Cook with Eya script
bouzid sidibouzid --script-only   # today's Sidi Bouzid story (~3 min of script)
bouzid sidibouzid                 # full audio for the Sidi Bouzid show
bouzid shows                      # list every preset
bouzid show farmer --script-only  # any preset by slug
```

**Good:** each prints Arabic content; full runs produce an mp3 path.

## 7. Air something & schedule

```bash
bouzid news --upload      # generate + push to the station
bouzid breaking "خبر عاجل تجريبي من إذاعة بوزيد"   # urgent flash, on air now
bouzid now                # shows what's playing
bouzid schedule --dry-run # preview the weekly grid without changing anything
bouzid schedule           # apply it for real
```

**Good:** `bouzid now` shows your uploaded item or scheduled music.

## 8. Music library (optional, downloads from YouTube)

```bash
bouzid fetch --list       # show what would be fetched (no download)
bouzid fetch --bg         # download in the background
bouzid fetch-status       # progress
bouzid rescan             # import the library + apply schedule
```

**Good:** files land in `$(bouzid music-dir)` and appear in AzuraCast after
rescan.

---

## Cleanup

```bash
bouzid stop                 # stop the stack
bouzid uninstall            # remove containers + volumes, KEEP your music
bouzid uninstall --purge-music   # also delete the music library
```

## If something fails
- **Key/auth errors on news or say:** your `GEMINI_API_KEY` in `.env` is wrong.
- **429 / rate-limit while voicing:** lower `GOOGLE_TTS_RPM` in `.env` (default
  15) and re-run; the engine self-throttles and retries.
- **`bouzid` not found:** re-run `./install.sh`, or call scripts directly with
  `python3 scripts/<name>.py`.
- **Health shows DOWN right after start:** AzuraCast needs a couple of minutes
  on first boot — wait and re-run `bouzid health`.
