# Bouzid FM 2.0 — first run, step by step

This walks you from an empty machine to a Tunisian AI station on air. The whole
station is driven from one TUI, so most of your time is in `bouzid tui`.

---

## 1. Install

```bash
unzip bouzid-fm-1.0.0.zip
cd bouzid-fm-2.0
cp .env.example .env
```

Open `.env` and paste your Google key:

```
GEMINI_API_KEY=YOUR_KEY_FROM_aistudio.google.com
```

Get the key (free) at https://aistudio.google.com/apikey . Everything else has
sensible defaults: the voice is **Kore**, text is **gemini-3.5-flash**, speech
is **gemini-3.1-flash-tts-preview**, and the throttle is 15 req/min.

Then:

```bash
./install.sh
```

This builds a Python venv, starts the Docker stack (AzuraCast + n8n) and installs
the `bouzid` command.

## 2. Open the control panel

```bash
bouzid tui
```

You land on **Dashboard**. Jump between tabs with single keys:
`d` Dashboard · `n` News · `s` Shows · `i` Imaging · `m` Music · `g` Settings.
The box at the bottom is the Activity log — every action streams its output
there. `Ctrl+L` clears it, `q` quits.

## 3. Bring the stack up

On **Dashboard**, press **Start stack**, wait a few seconds, then **Health**.
You want both AzuraCast and n8n reporting OK. (First-ever boot of AzuraCast
takes a couple of minutes while it initializes.)

## 4. Confirm your voice

Go to **Settings** (`g`). Your key should already be filled in. Pick a voice
from the dropdown (they're grouped female / male; Kore is the default female
voice), then press **Save settings** followed by **Test voice**. The log shows
it writing `/tmp/bouzid_voice_test.mp3` — play that file to hear the voice.

## 5. Make your first bulletin

Go to **News** (`n`) and press **Generate bulletin**. Bouzid pulls live RSS,
writes a Fus-ha bulletin with Gemini, voices it, and mixes the news imaging.
When you're happy, press **Generate + air** to upload it to the station.

Need something urgent? Type Arabic text in the breaking-news box and press
**Air breaking news** (leave it empty to flash the latest headlines).

## 6. Produce the signature shows

On **Shows** (`s`):
- **Cook — generate** builds the daily Tunisian cooking show.
- **Sidi Bouzid — generate** builds today's ~3-minute culture & history story.
- Pick any preset from the dropdown and press **Generate show**.

Use the **+ air** buttons to upload straight to the station.

## 7. Hear the imaging

On **Imaging** (`i`), choose a show and an element (intro / outro / beds /
transition) and press **Preview**. These are the real mastered files used under
the voice — no synthesized beeps.

## 8. Add music and schedule the week

On **Music** (`m`):
1. **Fetch trending** downloads trending Tunisian tracks into your library.
2. **Rescan into AzuraCast** imports them.
3. **Apply schedule** lays out the full weekly grid.

That's it — you have a live, self-programming Tunisian AI station.

## Tip: automate the news

The stack ships an n8n workflow (`n8n/bouzid-news-google-tts.json`). Open n8n at
http://localhost:5678 (admin / bouzid2026), import the workflow, set
`GEMINI_API_KEY` in n8n's environment, and activate it to produce bulletins on
a timer.
