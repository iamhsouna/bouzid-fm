# Bouzid FM — show presets

Every talk show is one JSON file in this folder: `shows/<slug>.json`.
The generic engine (`scripts/show_engine.py`) reads it and produces a full
episode (LLM script → TTS voice → intro/bed/transition/outro mix → optional
push to AzuraCast). **Add a show = add a JSON file. No code changes.**

## Run

```bash
./bin/bouzid shows                 # list every show
./bin/bouzid show <slug> --script-only   # preview the script (no audio)
./bin/bouzid show <slug> --play          # full audio, play locally
./bin/bouzid show <slug> --upload        # generate + push to the station
```

## Fields

| field | required | meaning |
|---|---|---|
| `slug` | — | defaults to the filename; the command name |
| `name` | ✅ | display name (Arabic) |
| `emoji` | | shown in lists / script headers |
| `desc` | | one-line description |
| `length_min` | | target length hint (used only inside the prompt text) |
| `prompt` | ✅ | the persona + rules for the LLM. `{date}` is replaced with today. **Do not** add the `[[marker]]` format here — the engine appends it from `markers`. |
| `markers` | | ordered segments: `[{ "id": "تاريخ", "display": "📜 التاريخ", "hint": "..." }]`. One marker = one segment (no transitions). Omit for a single free-form segment. |
| `angles` | | optional list; one is chosen per weekday so daily episodes stay fresh |
| `inspiration` | | optional fact bank; `inspiration_n` (default 3) are sampled per run |
| `feed` | | `none` (default), `weather`, or `prayer` — injects live data (see `scripts/feeds.py`) |
| `mix` | | `{ "intro": true, "outro": true, "bed": true, "transition": true }`. Turn intro/outro off for short stingers. |
| `assets_subdir` | | use `assets/<subdir>/{intro,outro,beds,transition}` instead of the station defaults |
| `bed_gain` | | music-bed volume under the voice (default `0.12`) |
| `upload_folder` | | AzuraCast destination folder (default = capitalized slug) |
| `music_subdir` | | where episodes are saved under `$MUSIC_DIR` (default = slug) |
| `fallback` | | offline episode: `[{ "id": "<marker id>", "text": "..." }]`, aired when no LLM is reachable |

## Per-show env overrides (optional, in `.env`)

- `<SLUG>_BACKEND` — TTS engine just for this show (`edge` / `google` / `eleven` / `xtts`)
- `<SLUG>_VOICE` — a specific voice id
- `BOUZID_<SLUG>_VOICE_FX` — `0` to disable voice FX for this show

e.g. `PROVERB_BACKEND=eleven`, `FARMER_VOICE=ar-TN-ReemNeural`.

## Regenerate the bundled shows

```bash
python3 scripts/seed_presets.py          # writes any missing presets (safe)
python3 scripts/seed_presets.py --force  # overwrite with the shipped defaults
python3 scripts/seed_presets.py --list   # list what the seeder defines
```
