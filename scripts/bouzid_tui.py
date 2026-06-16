#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — the control panel (unified, Google-only).

A fully automated AI web/FM radio station, driven from one clean panel with a
single, logical flow:

    Dashboard  →  Studio  →  Library  →  Imaging  →  Jingles  →  Music  →  Settings

Unified control (this is the whole point of the rewrite):
  * STUDIO   one screen for EVERY talk show. News, Cook, Sidi Bouzid and all
             the preset shows are just presets you switch between — news is a
             preset like any other. Generate, air, or preview the script.
  * LIBRARY  one browser for every generated episode. Filter by show (or “All
             shows”, jingles, or the music pool) and act on any row with the
             same buttons everywhere.
  * IMAGING  one screen to generate intros / outros / beds / transitions for
             ALL shows at once, one show, one kind, or one kind of one show.
             Every run adds a new variant; the app always uses the newest one
             unless you pin a specific variant as the default you like.

Long actions run in a background worker and stream into the Activity log, so
the UI never freezes. The panel shells out to the same scripts the CLI uses
(show_engine.py, library.py, make_imaging.py, assets_lib.py, …) so the TUI and
the `bouzid` command always behave identically.

Run it with:  bouzid tui   (or:  python3 scripts/bouzid_tui.py)
"""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import tts_engine
    GEMINI_VOICES = tts_engine.GEMINI_VOICES
except Exception:  # noqa: BLE001
    GEMINI_VOICES = [("Kore", "female", "default")]

try:
    import library
except Exception:  # noqa: BLE001
    library = None

try:
    import assets_lib
except Exception:  # noqa: BLE001
    assets_lib = None

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Log, Select, Static,
    TabbedContent, TabPane, TextArea,
)

ENV_PATH = os.path.join(ROOT, ".env")
SHOWS_DIR = os.path.join(ROOT, "shows")
ASSETS_DIR = os.path.join(ROOT, "assets")


def _app_version():
    """Read the app version from the VERSION file at the repo root."""
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


APP_VERSION = _app_version()

# The imaging roster is derived from the presets (+ the shared "station" set)
# by the asset library — there is no separate, hardcoded show list to drift out
# of sync with Studio. Used only by the Jingles tab's bed/SFX picker; the
# Imaging tab's Show picker mirrors Studio's preset list exactly.
def _imaging_shows():
    try:
        return assets_lib.imaging_shows()
    except Exception:  # noqa: BLE001
        return ["station"]


# Audio extensions recognised as jingles (matches make_jingle / use_jingle).
_JINGLE_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
                ".flac", ".wav", ".wma")


def _jingle_pool_dir():
    """The flat jingle pool (matches make_jingle.jingles_dir / use_jingle)."""
    md = env_get("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    return os.path.join(md, "jingles")


def _jingle_pool_names():
    """Newest-first list of jingle basenames in the pool (for the picker)."""
    d = _jingle_pool_dir()
    if not os.path.isdir(d):
        return []
    entries = []
    for base, _dirs, names in os.walk(d):
        for n in names:
            if n.lower().endswith(_JINGLE_EXTS) and not n.startswith("."):
                p = os.path.join(base, n)
                entries.append((os.path.getmtime(p), n))
    entries.sort(reverse=True)  # newest first
    return [name for _t, name in entries]


IMAGING_SHOWS = _imaging_shows()
# Imaging kinds the generator + resolver understand.
IMAGING_KINDS = ["intro", "outro", "bed", "transition", "sting"]

# Hardcoded AzuraCast key fallback so Skip / Air / upload work out-of-the-box.
AZURACAST_API_KEY_FALLBACK = "f9cf1ebc9e90e511:55b5f72fe1c735eabc20c0270d65a453"


# --------------------------------------------------------------------------- #
#  .env helpers
# --------------------------------------------------------------------------- #
def env_get(key, default=""):
    val = os.environ.get(key)
    if val is not None:
        return val
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return default


def set_env_values(updates):
    """Upsert KEY=VALUE pairs into .env, preserving the rest of the file."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append("%s=%s" % (key, remaining.pop(key)))
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append("%s=%s" % (key, val))
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    for key, val in updates.items():
        os.environ[key] = val


def music_root():
    md = env_get("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    if not os.path.isabs(md):
        md = os.path.join(ROOT, md)
    return md


def list_show_slugs():
    slugs = []
    for path in sorted(glob.glob(os.path.join(SHOWS_DIR, "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        if slug.startswith("_"):
            continue
        slugs.append(slug)
    return slugs


def _preset_label(slug):
    """A friendly '<emoji> <title> (<slug>)' label read straight from the JSON."""
    try:
        with open(os.path.join(SHOWS_DIR, slug + ".json"), encoding="utf-8") as f:
            d = json.load(f)
        emoji = d.get("emoji") or "\U0001F399"
        title = d.get("title_en") or d.get("name") or slug
        return "%s  %s  (%s)" % (emoji, title, slug)
    except Exception:  # noqa: BLE001
        return slug


def preset_options():
    return [(_preset_label(s), s) for s in list_show_slugs()]


def library_filter_options():
    """All shows + each preset + jingles + the music pool."""
    opts = [("\U0001F310  All shows (everything)", "all")]
    opts += preset_options()
    opts += [("\U0001F3DA  Jingles", "jingles"),
             ("\U0001F3B6  Music pool", "music")]
    return opts


def voice_options(include_auto=False):
    opts = []
    if include_auto:
        opts.append(("Auto \u2014 each show's own voice", "__auto__"))
    for v, g, note in GEMINI_VOICES:
        opts.append(("%s  \u2014  %s, %s" % (v, g, note), v))
    return opts


# --------------------------------------------------------------------------- #
#  The app
# --------------------------------------------------------------------------- #
class BouzidApp(App):
    TITLE = "Bouzid FM"
    SUB_TITLE = "v%s" % APP_VERSION
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    .pad { padding: 1 2; }
    .row { height: auto; }
    .hint { color: $text-muted; margin-bottom: 1; }
    .section { text-style: bold; color: $accent; margin-top: 1; }
    Button { margin: 1 1 0 0; }
    Input, Select { margin-bottom: 1; }
    #log { height: 12; border: round $primary; padding: 0 1; }
    #logbar { height: auto; }
    DataTable { height: auto; max-height: 14; margin-bottom: 1; }
    /* Review box for the drafted jingle script. Without an explicit height the
       TextArea collapses inside the scroll container, so a Gemini-drafted
       script is loaded but never visible -- the "where is the review box?"
       bug. Pin it to a fixed, scrollable editing area. */
    #jingle-script { height: 8; border: round $accent; margin-bottom: 1; }
    #ver { height: 1; color: $text-muted; text-align: right; padding: 0 2; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("d", "show_tab('tab-dash')", "Dashboard"),
        ("p", "show_tab('tab-studio')", "Studio"),
        ("l", "show_tab('tab-library')", "Library"),
        ("i", "show_tab('tab-imaging')", "Imaging"),
        ("j", "show_tab('tab-jingles')", "Jingles"),
        ("m", "show_tab('tab-music')", "Music"),
        ("g", "show_tab('tab-settings')", "Settings"),
        ("ctrl+l", "clear_log", "Clear log"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="body", initial="tab-dash"):
            with TabPane("\U0001F4FB Dashboard", id="tab-dash"):
                yield from self._dash_tab()
            with TabPane("\U0001F399 Studio", id="tab-studio"):
                yield from self._studio_tab()
            with TabPane("\U0001F5C2 Library", id="tab-library"):
                yield from self._library_tab()
            with TabPane("\U0001F3B5 Imaging", id="tab-imaging"):
                yield from self._imaging_tab()
            with TabPane("\U0001F39A Jingles", id="tab-jingles"):
                yield from self._jingles_tab()
            with TabPane("\U0001F3B6 Music", id="tab-music"):
                yield from self._music_tab()
            with TabPane("\u2699 Settings", id="tab-settings"):
                yield from self._settings_tab()
        with Horizontal(id="logbar"):
            yield Button("Clear log", id="log-clear", variant="warning")
        yield Log(id="log", highlight=True)
        yield Static("\U0001F4FB Bouzid FM \u2014 v%s" % APP_VERSION,
                     id="ver")
        yield Footer()

    # ---- Dashboard ------------------------------------------------------- #
    def _dash_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Bouzid FM \u2014 station control", classes="section")
            yield Static("Start the stack, see what's on air, and run quick "
                         "actions.", classes="hint")
            with Horizontal(classes="row"):
                yield Button("Start stack", id="dash-start", variant="success")
                yield Button("Stop", id="dash-stop", variant="error")
                yield Button("Restart", id="dash-restart")
                yield Button("Health", id="dash-health")
            with Horizontal(classes="row"):
                yield Button("Now playing", id="dash-now", variant="primary")
                yield Button("\u25c0 Previous track", id="dash-prev")
                yield Button("Skip track \u25b6", id="dash-skip")
                yield Button("Show URLs", id="dash-urls")

    # ---- Studio: one control screen for every show ----------------------- #
    def _studio_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Studio \u2014 one control for every show",
                         classes="section")
            yield Static("Pick a preset and generate it. News is just a preset "
                         "too: it pulls live headlines automatically. Leave "
                         "the text box empty for the normal AI episode, or "
                         "type custom text to speak instead (e.g. a breaking-"
                         "news flash for the news preset).", classes="hint")
            yield Label("Show (preset)")
            opts = preset_options()
            init = opts[0][1] if opts else Select.BLANK
            yield Select(opts or [("(no presets)", "")], id="studio-pick",
                         value=init, allow_blank=True)
            yield Label("Custom text to speak (optional)")
            yield Input(placeholder="اتركه فارغاً للحلقة العادي��، أو اكتب نصّاً خاصّاً…",
                        id="studio-text")
            with Horizontal(classes="row"):
                yield Button("\U0001F3AC Generate", id="studio-gen",
                             variant="primary")
                yield Button("\U0001F4E1 Generate + air", id="studio-air",
                             variant="success")
                yield Button("\U0001F4DD Preview script", id="studio-script")
                yield Button("\u21bb Refresh presets", id="studio-refresh")
            yield Static("Remaster \u2014 perfect the mix before airing",
                         classes="section")
            yield Static("Re-mix the NEWEST generation of the show above with "
                         "tuned parameters (no re-synthesis \u2014 fast & free). "
                         "Leave a field empty to keep its default. Bed volume "
                         "and output loudness are the ones to nudge if beds "
                         "feel low.", classes="hint")
            with Horizontal(classes="row"):
                yield Input(placeholder="bed vol (0.72)", id="rm-bed")
                yield Input(placeholder="ducking 0..1 (0.55)", id="rm-duck")
                yield Input(placeholder="voice vol (1.0)", id="rm-voice")
                yield Input(placeholder="loudness LUFS (-12)", id="rm-lufs")
            with Horizontal(classes="row"):
                yield Input(placeholder="intro gain (1.0)", id="rm-introg")
                yield Input(placeholder="outro gain (1.0)", id="rm-outrog")
                yield Input(placeholder="overlap s (0.0)", id="rm-overlap")
                yield Input(placeholder="fade in s (1.0)", id="rm-fadein")
                yield Input(placeholder="fade out s (2.0)", id="rm-fadeout")
            with Horizontal(classes="row"):
                yield Button("\U0001F39B Remaster preview",
                             id="studio-remaster", variant="warning")
                yield Button("\U0001F4E1 Remaster + air",
                             id="studio-remaster-air", variant="success")
            yield Static("Tip: manage everything you generate in the Library "
                         "tab \u2014 it lists every show in one place.",
                         classes="hint")

    # ---- Library: one filterable browser for everything ------------------ #
    def _library_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Library \u2014 every generated file in one place",
                         classes="section")
            yield Static("Filter by show (or All shows / Jingles / Music), "
                         "pick a row, then act on it. The same buttons work "
                         "for every filter. Delete & rename also sync to "
                         "AzuraCast.", classes="hint")
            yield Label("Filter")
            yield Select(library_filter_options(), id="lib-filter",
                         value="all", allow_blank=False)
            yield DataTable(id="lib-table")
            yield Input(placeholder="new name (used by Rename)\u2026",
                        id="lib-newname")
            with Horizontal(classes="row"):
                yield Button("\u25b6 Local play", id="lib-play",
                             variant="primary")
                yield Button("\U0001F4E1 Air now", id="lib-air",
                             variant="success")
                yield Button("Rename", id="lib-rename")
                yield Button("Duplicate", id="lib-dup")
                yield Button("Re-upload", id="lib-reupload")
                yield Button("Delete", id="lib-delete", variant="error")
                yield Button("\u21bb Refresh", id="lib-refresh")
            with Horizontal(classes="row"):
                yield Button("\U0001F5d1 Empty this library", id="lib-empty",
                             variant="error")
                yield Button("\u21c4 Sync with AzuraCast", id="lib-sync",
                             variant="warning")

    # ---- Imaging: one screen to generate + curate all imaging ------------ #
    def _imaging_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Imaging \u2014 intros, outros, beds & transitions",
                         classes="section")
            yield Static("Same show list as the Studio tab \u2014 pick a preset "
                         "(plus a kind, or all kinds) and generate. Each run "
                         "adds a NEW variant; the station automatically uses "
                         "the newest one unless you pin a default below. "
                         "(Generation needs a paid Gemini key + network.)",
                         classes="hint")
            yield Label("Show (preset)")
            img_opts = preset_options()
            img_init = img_opts[0][1] if img_opts else Select.BLANK
            yield Select(img_opts or [("(no presets)", "")],
                         id="img-show", value=img_init, allow_blank=True)
            yield Label("Kind")
            yield Select([("All kinds", "all")]
                         + [(k, k) for k in IMAGING_KINDS],
                         id="img-kind", value="intro", allow_blank=False)
            yield Label("Voice for spoken intros/outros (optional)")
            yield Select(voice_options(include_auto=True), id="img-voice",
                         value="__auto__", allow_blank=False)
            with Horizontal(classes="row"):
                yield Button("\U0001F3AC Generate variant", id="img-gen",
                             variant="primary")
                yield Button("\u267b Regenerate (replace old)",
                             id="img-regen", variant="warning")
                yield Button("\u2605 Generate + pin default", id="img-gendef",
                             variant="success")
                yield Button("Show plan", id="img-plan")
            yield Static("Variants for the selected show + kind "
                         "(newest first; \u2605 = the default you pinned):",
                         classes="section")
            yield DataTable(id="img-table")
            with Horizontal(classes="row"):
                yield Button("\u25b6 Preview", id="img-play", variant="primary")
                yield Button("\u2605 Set as default", id="img-setdef",
                             variant="success")
                yield Button("Clear default (use latest)", id="img-cleardef")
                yield Button("Delete variant", id="img-del", variant="error")
                yield Button("\u21bb Refresh", id="img-refresh")

    # ---- Jingles --------------------------------------------------------- #
    def _jingles_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("\U0001F39A Jingle studio", classes="section")
            yield Static("Build short station IDs: a Gemini-written Arabic ID "
                         "spoken over a mastered bed, a clean voice-only ID, "
                         "or a pure SFX sting montage. They land in the "
                         "jingles library (see the Library tab, filter "
                         "\"Jingles\").", classes="hint")
            yield Label("Type")
            yield Select(
                [("\U0001F399 Voiced \u2014 script over a music bed", "voiced"),
                 ("\U0001F5E3 Voice only \u2014 clean voice, no bed", "voiceonly"),
                 ("\U0001F39A SFX only \u2014 sting, no voice", "sfx")],
                id="jingle-mode", value="voiced", allow_blank=False)
            yield Label("Imaging set (mastered SFX / bed to use)")
            yield Select([(s, s) for s in IMAGING_SHOWS], id="jingle-set",
                         value=IMAGING_SHOWS[0], allow_blank=False)
            yield Label("Idea / prompt (Gemini drafts the script from this)")
            yield Input(
                placeholder="e.g. ترحيب صباحي مفعم بالطاقة بالمستمعين…",
                id="jingle-prompt")
            with Horizontal(classes="row"):
                yield Button("\u270d Draft script with Gemini",
                             id="jingle-draft", variant="primary")
            yield Label("Script to speak \u2014 review/edit, or type your own")
            yield TextArea("", id="jingle-script")
            yield Label("Voice")
            yield Select(voice_options(), id="jingle-voice",
                         value=env_get("GEMINI_TTS_VOICE", "Kore"),
                         allow_blank=False)
            yield Label("Music bed (voiced)")
            yield Select(
                [("Mastered assets \u2014 your imaging", "assets"),
                 ("Lyria 3 \u2014 generate with Google", "lyria"),
                 ("Eleven Music \u2014 generate", "eleven")],
                id="jingle-bed-source", value="assets", allow_blank=False)
            yield Label("SFX source (SFX-only mode)")
            yield Select(
                [("Mastered montage \u2014 your stings", "assets"),
                 ("ElevenLabs \u2014 generate a sting", "eleven")],
                id="jingle-sfx-source", value="assets", allow_blank=False)
            yield Label("Generation prompt (Lyria / Eleven bed or SFX)")
            yield Input(
                placeholder="e.g. warm Tunisian oud radio bed, instrumental\u2026",
                id="jingle-gen-prompt")
            yield Label("Name (optional)")
            yield Input(placeholder="morning_id (auto-named if blank)\u2026",
                        id="jingle-name")
            with Horizontal(classes="row"):
                yield Button("\U0001F39B Generate jingle", id="jingle-gen",
                             variant="success")
                yield Button("Generate + air", id="jingle-air",
                             variant="warning")
            yield Static("Promote a jingle to a show role — pick the jingle "
                         "from the pool, then the target show + role.",
                         classes="hint")
            yield Label("Jingle from the pool")
            yield Select([("(no jingles yet \u2014 generate one above)", "")]
                         + [(name, name) for name in _jingle_pool_names()],
                         id="jingle-use-pick", allow_blank=False)
            yield Label("Target show")
            yield Select([(s, s) for s in IMAGING_SHOWS], id="jingle-use-show",
                         value=IMAGING_SHOWS[0], allow_blank=False)
            yield Label("Role")
            yield Select([(k, k) for k in IMAGING_KINDS], id="jingle-use-role",
                         value=IMAGING_KINDS[0], allow_blank=False)
            with Horizontal(classes="row"):
                yield Button("\U0001F517 Use as\u2026", id="jingle-use",
                             variant="primary")
                yield Button("\u21bb Refresh pool", id="jingle-refresh")

    # ---- Music ----------------------------------------------------------- #
    def _music_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Music library & scheduling", classes="section")
            yield Static("Library: %s  \u2014  browse the tracks in the "
                         "Library tab (filter \"Music pool\")." % music_root(),
                         classes="hint")
            yield Label("Category (crate)")
            yield Select(self._music_crate_options(), id="music-crate",
                         allow_blank=False)
            yield Label("Anghami playlist URL for this category")
            yield Input(placeholder="https://play.anghami.com/playlist/...",
                        id="music-pl")
            yield Static("", id="music-pl-hint", classes="hint")
            with Horizontal(classes="row"):
                yield Button("\U0001F4be Save playlist", id="music-save-pl",
                             variant="primary")
                yield Button("Reset to default", id="music-reset-pl")
                yield Button("\U0001F5d1 Clear this category",
                             id="music-clear", variant="error")
            with Horizontal(classes="row"):
                yield Button("\u2b07 Fetch this category",
                             id="music-fetch-one", variant="success")
                yield Button("\u2b07 Fetch ALL categories",
                             id="music-fetch-all", variant="warning")
                yield Button("Fetch progress", id="music-status")
                yield Button("Rescan into AzuraCast", id="music-rescan")
            yield Static("Weekly schedule", classes="section")
            with Horizontal(classes="row"):
                yield Button("Preview grid", id="sched-grid")
                yield Button("Apply schedule", id="sched-apply",
                             variant="success")
                yield Button("Apply (reset)", id="sched-reset",
                             variant="warning")

    # ---- Settings -------------------------------------------------------- #
    def _settings_tab(self):
        with VerticalScroll(classes="pad"):
            yield Static("Google Gemini \u2014 the only provider",
                         classes="section")
            yield Static("Text and voice both run on Google Gemini. Set your "
                         "key and pick the station voice.", classes="hint")
            yield Label("API key (GEMINI_API_KEY)")
            yield Input(value=env_get("GEMINI_API_KEY"), password=True,
                        id="set-key")
            yield Label("Station voice (GEMINI_TTS_VOICE)")
            yield Select(voice_options(), id="set-voice",
                         value=env_get("GEMINI_TTS_VOICE", "Kore"),
                         allow_blank=False)
            yield Label("Speech model (GEMINI_TTS_MODEL)")
            yield Input(value=env_get("GEMINI_TTS_MODEL",
                                      "gemini-3.1-flash-tts-preview"),
                        id="set-tts-model")
            yield Label("Text model (GEMINI_TEXT_MODEL)")
            yield Input(value=env_get("GEMINI_TEXT_MODEL", "gemini-3.5-flash"),
                        id="set-text-model")
            yield Label("Self-throttle req/min (GOOGLE_TTS_RPM)")
            yield Input(value=env_get("GOOGLE_TTS_RPM", "15"), id="set-rpm")
            yield Label("AzuraCast API key (AZURACAST_API_KEY) \u2014 controls "
                        "Skip / Air / upload")
            yield Input(
                value=env_get("AZURACAST_API_KEY", AZURACAST_API_KEY_FALLBACK),
                password=True, id="set-az-key")
            with Horizontal(classes="row"):
                yield Button("Save settings", id="set-save", variant="success")
                yield Button("Test voice", id="set-test", variant="primary")
                yield Button("List voices", id="set-list")
            yield Static("Maintenance \u2014 assets & AzuraCast sync",
                         classes="section")
            yield Static("Reset assets DELETES the whole assets/ folder (a "
                         "timestamped backup is kept) and rebuilds the clean, "
                         "EMPTY intro/outro/bed/transition/sting folders for "
                         "every show, then triggers an AzuraCast media "
                         "rescan. No audio is regenerated \u2014 use the "
                         "Imaging tab afterwards. Sync media does a two-way "
                         "reconcile of every library with AzuraCast (upload "
                         "new, remove deleted). Both ask for a confirm click.",
                         classes="hint")
            with Horizontal(classes="row"):
                yield Button("\U0001F5d1 Reset assets (rebuild from scratch)",
                             id="set-reset-assets", variant="error")
                yield Button("\u21c4 Sync media \u2192 AzuraCast",
                             id="set-sync-az", variant="warning")

    # ---- mount ----------------------------------------------------------- #
    def on_mount(self):
        self._lib_rows = []
        self._img_rows = []
        self._jobs = {}
        self._job_labels = {}
        self._armed = None
        self._armed_label = None
        try:
            t = self.query_one("#lib-table", DataTable)
            t.cursor_type = "row"
            t.zebra_stripes = True
            t.add_columns("Show", "Episode", "Length", "Size", "Created",
                          "On air")
        except Exception:  # noqa: BLE001
            pass
        try:
            it = self.query_one("#img-table", DataTable)
            it.cursor_type = "row"
            it.zebra_stripes = True
            it.add_columns("Kind", "Variant", "Created", "Size", "Default")
        except Exception:  # noqa: BLE001
            pass
        self._lib_refresh()
        self._img_refresh()
        try:
            self._music_load_pl()
        except Exception:  # noqa: BLE001
            pass

    def on_select_changed(self, event: Select.Changed) -> None:
        sid = getattr(event.select, "id", None)
        if sid == "lib-filter":
            self._lib_refresh()
        elif sid in ("img-show", "img-kind"):
            self._img_refresh()
        elif sid == "music-crate":
            self._music_load_pl()

    # ===================================================================== #
    #  Library tab
    # ===================================================================== #
    def _lib_filter(self):
        v = self.query_one("#lib-filter", Select).value
        if not v or v is Select.BLANK:
            return "all"
        return str(v)

    def _lib_refresh(self):
        if library is None:
            return
        flt = self._lib_filter()

        def work():
            try:
                rows = library.list_episodes(
                    flt, with_duration=(flt != "music"))
            except Exception as e:  # noqa: BLE001
                rows = []
                self.call_from_thread(
                    self.log_line, "  \u26a0 could not list %s: %s" % (flt, e))
            self.call_from_thread(self._lib_populate, rows)

        self.run_worker(work, thread=True, exclusive=False, group="lib")

    def _lib_populate(self, rows):
        try:
            table = self.query_one("#lib-table", DataTable)
        except Exception:  # noqa: BLE001
            return
        table.clear()
        self._lib_rows = rows
        for r in rows:
            table.add_row(r.get("slug") or "\u2014", r["name"],
                          r.get("duration") or "\u2014", r["size"],
                          r["mtime"], r.get("on_air") or "\u2014")

    def _lib_selected(self):
        rows = self._lib_rows or []
        if not rows:
            self.log_line("  \u2717 nothing here yet \u2014 generate one first")
            return None
        try:
            idx = self.query_one("#lib-table", DataTable).cursor_row
        except Exception:  # noqa: BLE001
            idx = 0
        if idx is None or idx < 0 or idx >= len(rows):
            idx = 0
        return rows[idx]

    def _lib_action(self, action):
        if action == "refresh":
            return self._lib_refresh()
        if action == "empty":
            return self._lib_empty()
        if action == "sync":
            return self._lib_sync()
        row = self._lib_selected()
        if not row:
            return
        # Each row carries its own slug, so "All shows" still acts correctly.
        slug = row.get("slug") or self._lib_filter()
        path, name = row["path"], row["name"]
        refresh = self._lib_refresh
        if action == "play":
            return self._preview(path, "Previewing %s" % name,
                                 btn_id="lib-play")
        if action == "air":
            if self._job_running("lib-air"):
                return self._cancel_job("lib-air", "airing")
            return self.run_cmd(
                self.py("library.py", "air", slug, path),
                "Airing %s" % name, on_done=refresh, track_btn="lib-air")
        if action == "reupload":
            return self.run_cmd(
                self.py("library.py", "reupload", slug, path),
                "Re-uploading %s" % name, on_done=refresh)
        if action == "dup":
            return self.run_cmd(
                self.py("library.py", "duplicate", slug, path),
                "Duplicating %s" % name, on_done=refresh)
        if action == "delete":
            return self.run_cmd(self.py("library.py", "delete", slug, path),
                                "Deleting %s" % name, on_done=refresh)
        if action == "rename":
            newname = self.query_one("#lib-newname", Input).value.strip()
            if not newname:
                return self.log_line("  \u2717 type a new name first")
            return self.run_cmd(
                self.py("library.py", "rename", slug, path, newname),
                "Renaming %s" % name, on_done=refresh)
        return self.log_line("  \u2717 unknown action: %s" % action)

    def _lib_empty(self):
        flt = self._lib_filter()
        btn = "lib-empty"
        if getattr(self, "_armed", None) != btn:
            self._arm(btn, "\u26a0 Confirm \u2014 delete ALL")
            return self.log_line(
                "  \u26a0 This permanently deletes EVERY '%s' file locally AND "
                "from AzuraCast. Click the red button again to confirm." % flt)
        self._disarm()
        if flt == "all":
            return self.log_line(
                "  \u2717 pick a specific show/jingles/music to empty "
                "(All shows is read-only for safety)")
        return self.run_cmd(
            self.py("library.py", "empty", flt),
            "Emptying the %s library" % flt, on_done=self._lib_refresh)

    def _lib_sync(self):
        """Two-way reconcile the selected library (or all) with AzuraCast."""
        flt = self._lib_filter()
        btn = "lib-sync"
        if getattr(self, "_armed", None) != btn:
            self._arm(btn, "\u26a0 Confirm \u2014 sync now")
            return self.log_line(
                "  \u26a0 Two-way sync of '%s': uploads NEW local files to "
                "AzuraCast AND removes AzuraCast files that are missing "
                "locally, so the two stay in match. Click again to confirm."
                % flt)
        self._disarm()
        return self.run_cmd(
            self.py("library.py", "sync", flt),
            "Syncing %s with AzuraCast" % flt, on_done=self._lib_refresh)

    # ===================================================================== #
    #  Studio: remaster the newest generation with tuned parameters
    # ===================================================================== #
    def _latest_show_file(self, slug):
        """Newest generated .mp3 for a show that carries a .master.json sidecar
        (so it can be re-mixed). Returns an absolute path or None."""
        subdir = slug
        try:
            with open(os.path.join(SHOWS_DIR, slug + ".json"),
                      encoding="utf-8") as f:
                subdir = json.load(f).get("music_subdir") or slug
        except Exception:  # noqa: BLE001
            pass
        d = os.path.join(music_root(), subdir)
        cands = [p for p in glob.glob(os.path.join(d, "*.mp3"))
                 if os.path.exists(p + ".master.json")]
        cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cands[0] if cands else None

    def _studio_remaster(self, air=False):
        slug = self.query_one("#studio-pick", Select).value
        if not slug or slug is Select.BLANK:
            return self.log_line("  \u2717 pick a show first")
        slug = str(slug)
        src = self._latest_show_file(slug)
        if not src:
            return self.log_line(
                "  \u2717 no remasterable generation found for %s \u2014 "
                "generate an episode first, then remaster it" % slug)
        args = self.py("show_engine.py", "--remaster", src)
        params = (("rm-bed", "--bed-gain"), ("rm-duck", "--duck"),
                  ("rm-voice", "--voice-gain"), ("rm-lufs", "--lufs"),
                  ("rm-introg", "--intro-gain"), ("rm-outrog", "--outro-gain"),
                  ("rm-overlap", "--overlap"), ("rm-fadein", "--fade-in"),
                  ("rm-fadeout", "--fade-out"))
        for iid, flag in params:
            try:
                val = self.query_one("#" + iid, Input).value.strip()
            except Exception:  # noqa: BLE001
                val = ""
            if val:
                args += [flag, val]
        args.append("--air" if air else "--play")
        verb = "Remastering + airing" if air else "Remastering (preview)"
        return self.run_cmd(args, "%s %s" % (verb, slug),
                            on_done=self._lib_refresh)

    # ===================================================================== #
    #  Imaging tab
    # ===================================================================== #
    def _img_scope(self):
        show = self.query_one("#img-show", Select).value
        kind = self.query_one("#img-kind", Select).value
        return str(show or "all"), str(kind or "all")

    def _img_refresh(self):
        if assets_lib is None:
            return
        show, kind = self._img_scope()
        try:
            table = self.query_one("#img-table", DataTable)
        except Exception:  # noqa: BLE001
            return
        table.clear()
        if not show or show == "all":
            self._img_rows = []
            return
        # A specific kind lists just that kind; "All kinds" aggregates every
        # kind for the show, so freshly generated variants are always visible.
        kinds = [kind] if (kind and kind != "all") else assets_lib.kinds_for(show)
        rows = []
        for k in kinds:
            try:
                rows.extend(assets_lib.variants(show, k))
            except Exception as e:  # noqa: BLE001
                self.log_line("  \u26a0 could not list %s/%s: %s"
                              % (show, k, e))
        self._img_rows = rows
        for r in rows:
            table.add_row(r.get("kind", ""), r["name"], r["mtime"], r["size"],
                          "\u2605 default" if r["is_default"] else "")
        if not rows:
            self.log_line("  \u2022 no variants yet for %s/%s \u2014 generate "
                          "one above" % (show, kind))

    def _img_selected(self):
        rows = self._img_rows or []
        if not rows:
            self.log_line("  \u2717 no variants listed \u2014 pick a show (and "
                          "optionally a kind), then Refresh")
            return None
        try:
            idx = self.query_one("#img-table", DataTable).cursor_row
        except Exception:  # noqa: BLE001
            idx = 0
        if idx is None or idx < 0 or idx >= len(rows):
            idx = 0
        return rows[idx]

    def _img_generate(self, replace=False, make_default=False):
        show, kind = self._img_scope()
        args = self.py("make_imaging.py")
        if show != "all":
            args += ["--show", show]
        if kind != "all":
            args += ["--kind", kind]
        voice = self.query_one("#img-voice", Select).value
        if voice and voice not in (Select.BLANK, "__auto__"):
            args += ["--voice", str(voice)]
        if replace:
            args.append("--replace")
        if make_default:
            if show == "all" or kind == "all":
                self.log_line("  \u2139 pinning a default needs a specific "
                              "show + kind; generating without pinning")
            else:
                args.append("--make-default")
        label = "Generating imaging (%s / %s)%s" % (
            show, kind, " + replace" if replace else "")
        return self.run_cmd(args, label, on_done=self._img_refresh)

    def _img_action(self, bid):
        if bid == "img-gen":
            return self._img_generate()
        if bid == "img-regen":
            return self._img_generate(replace=True)
        if bid == "img-gendef":
            return self._img_generate(make_default=True)
        if bid == "img-plan":
            show, kind = self._img_scope()
            args = self.py("make_imaging.py", "--list")
            if show != "all":
                args += ["--show", show]
            if kind != "all":
                args += ["--kind", kind]
            return self.run_cmd(args, "Imaging plan")
        if bid == "img-refresh":
            return self._img_refresh()
        # row-level actions act on the SELECTED row's own show + kind, so they
        # work even when the table is aggregating "All kinds".
        row = self._img_selected()
        if not row:
            return
        if bid == "img-play":
            return self._preview(row["path"], "Previewing %s" % row["name"],
                                 btn_id="img-play")
        if assets_lib is None:
            return self.log_line("  \u2717 asset library unavailable")
        show, kind = row.get("show"), row.get("kind")
        try:
            if bid == "img-setdef":
                assets_lib.set_default(show, kind, row["name"])
                self.log_line("  \u2605 pinned default %s/%s \u2192 %s"
                              % (show, kind, row["name"]))
            elif bid == "img-cleardef":
                if assets_lib.clear_default(show, kind):
                    self.log_line("  \u2713 cleared default for %s/%s "
                                  "(now uses the latest)" % (show, kind))
                else:
                    self.log_line("  \u2022 no default was set for %s/%s"
                                  % (show, kind))
            elif bid == "img-del":
                assets_lib.delete_variant(show, kind, row["name"])
                self.log_line("  \u2713 deleted %s" % row["name"])
        except Exception as e:  # noqa: BLE001
            return self.log_line("  \u2717 %s" % e)
        return self._img_refresh()

    # ===================================================================== #
    #  Jingle studio
    # ===================================================================== #
    def _jingle_draft(self):
        prompt = self.query_one("#jingle-prompt", Input).value.strip()
        if not prompt:
            return self.log_line(
                "  \u2717 type a short idea/prompt, then draft the script")
        return self.run_cmd(
            self.py("make_jingle.py", "draft", "--prompt", prompt),
            "Drafting the script with Gemini", on_done=self._load_draft)

    def _load_draft(self):
        try:
            with open(os.path.join(ROOT, ".jingle_draft.txt"),
                      encoding="utf-8") as f:
                txt = f.read().strip()
        except Exception:  # noqa: BLE001
            txt = ""
        if not txt:
            return self.log_line(
                "  \u2717 no script drafted \u2014 check GEMINI_API_KEY/model")
        try:
            ta = self.query_one("#jingle-script", TextArea)
            ta.load_text(txt)
            # Force a re-render of the review box: load_text edits the document
            # but inside a scroll container the widget can fail to repaint, so
            # the drafted script looks empty even though it was loaded.
            ta.refresh()
            self.log_line(
                "  \u2713 script in the review box \u2014 edit it, then Generate")
        except Exception:  # noqa: BLE001
            pass

    def _jingle_generate(self, air=False):
        mode = self.query_one("#jingle-mode", Select).value
        if mode in (None, Select.BLANK):
            mode = "voiced"
        sset = self.query_one("#jingle-set", Select).value
        if sset in (None, Select.BLANK):
            sset = IMAGING_SHOWS[0]
        name = self.query_one("#jingle-name", Input).value.strip()
        gen_prompt = self.query_one("#jingle-gen-prompt", Input).value.strip()

        if mode == "sfx":
            source = self.query_one("#jingle-sfx-source", Select).value
            if source in (None, Select.BLANK):
                source = "assets"
            args = self.py("make_jingle.py", "sfx", "--set", str(sset),
                           "--source", str(source))
            if source == "eleven" and gen_prompt:
                args += ["--sfx-prompt", gen_prompt]
        else:
            script = self.query_one("#jingle-script", TextArea).text.strip()
            if not script:
                return self.log_line(
                    "  \u2717 draft a script with Gemini (or type your own) "
                    "before you Generate")
            args = self.py("make_jingle.py", str(mode), "--script", script)
            voice = self.query_one("#jingle-voice", Select).value
            if voice and voice is not Select.BLANK:
                args += ["--voice", str(voice)]
            if mode == "voiced":
                args += ["--set", str(sset)]
                bed_source = self.query_one("#jingle-bed-source", Select).value
                if bed_source in (None, Select.BLANK):
                    bed_source = "assets"
                args += ["--bed-source", str(bed_source)]
                if bed_source in ("lyria", "eleven") and gen_prompt:
                    args += ["--bed-prompt", gen_prompt]
        if name:
            args += ["--name", name]
        if air:
            args.append("--air")
        label = "Generating %s jingle%s" % (mode, " + airing" if air else "")
        return self.run_cmd(args, label, on_done=self._lib_refresh)

    def _jingle_use(self):
        """Promote the selected jingle in the pool to a chosen show's role.

        Reads the jingle picker, the target show picker, and the role picker,
        then shells out to use_jingle.py which copies the jingle into
        assets/<show>/<role>/ and pins it as the default.
        """
        pick = self.query_one("#jingle-use-pick", Select).value
        show = self.query_one("#jingle-use-show", Select).value
        role = self.query_one("#jingle-use-role", Select).value
        if pick in (None, Select.BLANK, ""):
            return self.log_line("  \u2717 pick a jingle from the pool first")
        if show in (None, Select.BLANK) or role in (None, Select.BLANK):
            return self.log_line("  \u2717 pick a target show and role first")
        args = self.py("use_jingle.py", str(pick), "--for", str(show),
                       "--as", str(role))
        return self.run_cmd(args,
                            "Promoting %s to %s / %s" % (pick, show, role))

    def _jingle_refresh(self):
        """Rebuild the jingle pool selector with current pool files."""
        names = _jingle_pool_names()
        opts = [("(no jingles yet \u2014 generate one above)", "")]
        opts += [(n, n) for n in names]
        self.query_one("#jingle-use-pick", Select).set_options(opts)
        self.log_line("  \u21bb pool refreshed: %d jingle(s)" % len(names))

    # ===================================================================== #
    #  logging / workers
    # ===================================================================== #
    def log_line(self, text):
        self.query_one("#log", Log).write_line(text)

    def run_cmd(self, args, title, on_done=None, track_btn=None):
        self.log_line("\u25b6 %s" % title)
        self.run_worker(
            lambda: self._exec(args, on_done, track_btn), thread=True,
            exclusive=False, group="cmd")

    def _exec(self, args, on_done=None, track_btn=None):
        try:
            proc = subprocess.Popen(
                args, cwd=ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                env=os.environ.copy())
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.log_line, "  \u2717 failed to start: %s" % e)
            return
        if track_btn:
            self.call_from_thread(self._register_job, track_btn, proc)
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            self.call_from_thread(self.log_line, "  " + line.rstrip())
        proc.wait()
        tail = "  \u2713 done" if proc.returncode == 0 else \
            "  \u2717 exited with code %d" % proc.returncode
        self.call_from_thread(self.log_line, tail)
        if track_btn:
            self.call_from_thread(self._clear_job, track_btn)
        if on_done is not None:
            self.call_from_thread(on_done)

    # ---- cancelable background jobs ------------------------------------- #
    def _register_job(self, btn, proc):
        self._jobs[btn] = proc
        try:
            b = self.query_one("#" + btn, Button)
            self._job_labels[btn] = b.label
            b.label = "\u25a0 Cancel"
        except Exception:  # noqa: BLE001
            pass

    def _clear_job(self, btn):
        self._jobs.pop(btn, None)
        lbl = self._job_labels.pop(btn, None)
        if lbl is not None:
            try:
                self.query_one("#" + btn, Button).label = lbl
            except Exception:  # noqa: BLE001
                pass

    def _job_running(self, btn):
        proc = getattr(self, "_jobs", {}).get(btn)
        return proc is not None and proc.poll() is None

    def _cancel_job(self, btn, what="job"):
        proc = getattr(self, "_jobs", {}).get(btn)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.log_line("  \u25a0 cancelled %s (label resets when it unwinds)"
                      % what)

    def py(self, script, *extra):
        return [sys.executable, os.path.join(HERE, script), *extra]

    def bouzid(self, *extra):
        env_bin = os.path.join(ROOT, "bin", "bouzid")
        return ["bash", env_bin, *extra]

    # ---- local preview --------------------------------------------------- #
    def _stop_preview(self):
        proc = getattr(self, "_play_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        bid = getattr(self, "_play_btn", None)
        lbl = getattr(self, "_play_btn_label", None)
        if bid and lbl is not None:
            try:
                self.query_one("#" + bid, Button).label = lbl
            except Exception:  # noqa: BLE001
                pass
        self._play_proc = None
        self._play_btn = None
        self._play_btn_label = None

    def _preview(self, path, title, btn_id=None):
        cur = getattr(self, "_play_proc", None)
        same = (cur is not None and cur.poll() is None
                and getattr(self, "_play_btn", None) == btn_id)
        if same:
            self._stop_preview()
            return self.log_line("  \u25a0 stopped preview")
        self._stop_preview()
        if not path or not os.path.exists(path):
            return self.log_line("  \u2717 file not found")
        self.log_line("\u25b6 %s (click again to stop)" % title)
        try:
            proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                 path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return self.log_line("  \u2717 ffplay is not installed")
        self._play_proc = proc
        self._play_btn = btn_id
        self._play_btn_label = None
        if btn_id:
            try:
                b = self.query_one("#" + btn_id, Button)
                self._play_btn_label = b.label
                b.label = "\u25a0 Stop"
            except Exception:  # noqa: BLE001
                pass

        def watch():
            proc.wait()
            self.call_from_thread(self._preview_ended, proc)

        self.run_worker(watch, thread=True, exclusive=False, group="preview")

    def _preview_ended(self, proc):
        if getattr(self, "_play_proc", None) is not proc:
            return
        bid = getattr(self, "_play_btn", None)
        lbl = getattr(self, "_play_btn_label", None)
        if bid and lbl is not None:
            try:
                self.query_one("#" + bid, Button).label = lbl
            except Exception:  # noqa: BLE001
                pass
        self._play_proc = None
        self._play_btn = None
        self._play_btn_label = None
        self.log_line("  \u2713 finished preview")

    def on_unmount(self) -> None:
        self._stop_preview()
        for proc in list(getattr(self, "_jobs", {}).values()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass

    # ---- confirm helper -------------------------------------------------- #
    def _arm(self, btn, confirm_label):
        """Arm a destructive button: remember its current label and show a
        confirm label. The next press of the SAME button performs the action;
        pressing any other button disarms it (see on_button_pressed)."""
        self._disarm()
        self._armed = btn
        self._armed_label = None
        try:
            b = self.query_one("#" + btn, Button)
            self._armed_label = b.label
            b.label = confirm_label
        except Exception:  # noqa: BLE001
            pass

    def _disarm(self):
        btn = getattr(self, "_armed", None)
        if not btn:
            return
        self._armed = None
        lbl = getattr(self, "_armed_label", None)
        self._armed_label = None
        try:
            if lbl is not None:
                self.query_one("#" + btn, Button).label = lbl
        except Exception:  # noqa: BLE001
            pass

    # ---- events ---------------------------------------------------------- #
    def action_show_tab(self, tab_id):
        self.query_one(TabbedContent).active = tab_id

    def action_clear_log(self):
        self.query_one("#log", Log).clear()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if getattr(self, "_armed", None) and bid != self._armed:
            self._disarm()

        # Library
        if bid.startswith("lib-"):
            return self._lib_action(bid.split("-", 1)[1])

        # Imaging
        if bid.startswith("img-"):
            return self._img_action(bid)

        # Jingles
        if bid == "jingle-draft":
            return self._jingle_draft()
        if bid in ("jingle-gen", "jingle-air"):
            return self._jingle_generate(air=(bid == "jingle-air"))
        if bid == "jingle-use":
            return self._jingle_use()
        if bid == "jingle-refresh":
            return self._jingle_refresh()

        # Dashboard
        if bid == "dash-start":
            return self.run_cmd(self.bouzid("start"), "Starting the stack")
        if bid == "dash-stop":
            return self.run_cmd(self.bouzid("stop"), "Stopping the stack")
        if bid == "dash-restart":
            return self.run_cmd(self.bouzid("restart"), "Restarting the stack")
        if bid == "dash-health":
            return self.run_cmd(self.bouzid("health"), "Checking health")
        if bid == "dash-now":
            return self.run_cmd(self.py("azuracast.py", "nowplaying"),
                                "Now playing")
        if bid == "dash-skip":
            return self.run_cmd(self.py("azuracast.py", "skip"), "Skipping")
        if bid == "dash-prev":
            return self.run_cmd(self.py("azuracast.py", "previous"),
                                "Playing previous track")
        if bid == "dash-urls":
            return self.run_cmd(self.bouzid("urls"), "Admin URLs")

        # Studio (unified shows + news)
        if bid == "studio-refresh":
            sel = self.query_one("#studio-pick", Select)
            sel.set_options(preset_options())
            return self.log_line("  \u2713 %d presets" % len(list_show_slugs()))
        if bid in ("studio-remaster", "studio-remaster-air"):
            return self._studio_remaster(air=(bid == "studio-remaster-air"))
        if bid in ("studio-gen", "studio-air", "studio-script"):
            slug = self.query_one("#studio-pick", Select).value
            if not slug or slug is Select.BLANK:
                return self.log_line("  \u2717 pick a show first")
            text = self.query_one("#studio-text", Input).value.strip()
            args = self.py("show_engine.py", "--show", str(slug))
            if text:
                args += ["--text", text]
            if bid == "studio-air":
                args.append("--air")
            elif bid == "studio-script":
                args.append("--script-only")
            verb = {"studio-gen": "Generating", "studio-air": "Generating + airing",
                    "studio-script": "Script preview for"}[bid]
            return self.run_cmd(args, "%s %s" % (verb, slug),
                                on_done=self._lib_refresh)

        # Music
        if bid == "music-save-pl":
            return self._music_save_pl()
        if bid == "music-reset-pl":
            return self._music_save_pl(reset=True)
        if bid == "music-clear":
            return self._music_clear()
        if bid == "music-fetch-one":
            return self._music_fetch(all_crates=False)
        if bid == "music-fetch-all":
            return self._music_fetch(all_crates=True)
        if bid == "music-status":
            return self.run_cmd(self.py("fetch_status.py"), "Fetch progress")
        if bid == "music-rescan":
            return self.run_cmd(self.bouzid("rescan"), "Rescanning library")
        if bid == "sched-grid":
            return self.run_cmd(self.py("build_grid.py"), "Building grid")
        if bid == "sched-apply":
            return self.run_cmd(self.py("azuracast.py", "apply-schedule"),
                                "Applying schedule")
        if bid == "sched-reset":
            return self.run_cmd(
                self.py("azuracast.py", "apply-schedule", "--reset"),
                "Applying schedule (reset)")

        # Settings
        if bid == "set-save":
            return self._save_settings()
        if bid == "set-test":
            return self.run_cmd(
                self.py("tts_engine.py", "--text",
                        "مستمعي بوزيد إف إم، هذه تجربة صوت.",
                        "--out", "/tmp/bouzid_voice_test.mp3"),
                "Testing the Gemini voice")
        if bid == "set-list":
            return self.run_cmd(self.py("tts_engine.py", "--list-voices"),
                                "Gemini voices")
        if bid == "set-reset-assets":
            return self._reset_assets()
        if bid == "set-sync-az":
            return self._sync_all()
        if bid == "log-clear":
            return self.action_clear_log()

    def _reset_assets(self):
        """Wipe assets/ and rebuild the clean structure, then rescan AzuraCast."""
        btn = "set-reset-assets"
        if getattr(self, "_armed", None) != btn:
            self._arm(btn, "\u26a0 Confirm \u2014 wipe & rebuild")
            return self.log_line(
                "  \u26a0 This DELETES the entire assets/ folder (a backup is "
                "kept) and rebuilds the clean, empty per-show structure. No "
                "audio is regenerated; AzuraCast is rescanned afterwards. "
                "Click again to confirm.")
        self._disarm()
        return self.run_cmd(
            self.py("assets_lib.py", "reset"),
            "Resetting assets to a clean structure",
            on_done=self._after_reset_assets)

    def _after_reset_assets(self):
        self._img_refresh()
        self.run_cmd(self.bouzid("rescan"),
                     "Rescanning AzuraCast media after reset")

    def _sync_all(self):
        """Two-way reconcile EVERY library with AzuraCast."""
        btn = "set-sync-az"
        if getattr(self, "_armed", None) != btn:
            self._arm(btn, "\u26a0 Confirm \u2014 sync all")
            return self.log_line(
                "  \u26a0 Two-way sync of ALL shows: uploads NEW local files "
                "to AzuraCast AND removes AzuraCast files missing locally, so "
                "the two stay in match. Click again to confirm.")
        self._disarm()
        return self.run_cmd(
            self.py("library.py", "sync", "all"),
            "Syncing all libraries with AzuraCast",
            on_done=self._lib_refresh)

    # ===================================================================== #
    #  Music tab helpers (Anghami playlists -> crates)
    # ===================================================================== #
    def _music_cfg_path(self):
        return os.path.join(ROOT, "config", "music_playlists.json")

    def _load_music_cfg(self):
        try:
            with open(self._music_cfg_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {"crates": {}}

    def _music_crate_options(self):
        cfg = self._load_music_cfg()
        opts = []
        for key, spec in cfg.get("crates", {}).items():
            opts.append((spec.get("label") or key, key))
        return opts or [("(no crates configured)", "")]

    def _music_eff_url(self, crate):
        spec = self._load_music_cfg().get("crates", {}).get(crate, {})
        return (spec.get("override") or spec.get("default") or "").strip()

    def _music_crate(self):
        try:
            v = self.query_one("#music-crate", Select).value
        except Exception:  # noqa: BLE001
            return None
        if not v or v is Select.BLANK:
            return None
        return str(v)

    def _music_load_pl(self):
        """Fill the URL box + hint from the selected crate's config."""
        crate = self._music_crate()
        if not crate:
            return
        spec = self._load_music_cfg().get("crates", {}).get(crate, {})
        try:
            self.query_one("#music-pl", Input).value = self._music_eff_url(crate)
        except Exception:  # noqa: BLE001
            pass
        ov = (spec.get("override") or "").strip()
        default = (spec.get("default") or "").strip()
        if ov:
            txt = "Using your override \u2014 default is %s" % (default or "none")
        else:
            txt = "Using the researched default \u2014 edit + Save to override"
        try:
            self.query_one("#music-pl-hint", Static).update(txt)
        except Exception:  # noqa: BLE001
            pass

    def _music_save_pl(self, reset=False):
        crate = self._music_crate()
        if not crate:
            return self.log_line("  \u2717 pick a category first")
        if reset:
            return self.run_cmd(
                self.py("anghami_dl.py", "--reset", crate),
                "Resetting %s playlist to default" % crate,
                on_done=self._music_load_pl)
        url = self.query_one("#music-pl", Input).value.strip()
        return self.run_cmd(
            self.py("anghami_dl.py", "--set", crate, url),
            "Saving the %s playlist" % crate, on_done=self._music_load_pl)

    def _music_fetch(self, all_crates=False):
        if all_crates:
            btn = "music-fetch-all"
            if self._job_running(btn):
                return self._cancel_job(btn, "fetching")
            return self.run_cmd(
                self.py("anghami_dl.py", "--all"),
                "Fetching ALL categories from Anghami",
                on_done=self._lib_refresh, track_btn=btn)
        crate = self._music_crate()
        if not crate:
            return self.log_line("  \u2717 pick a category first")
        btn = "music-fetch-one"
        if self._job_running(btn):
            return self._cancel_job(btn, "fetching")
        return self.run_cmd(
            self.py("anghami_dl.py", "--crate", crate),
            "Fetching %s from Anghami" % crate,
            on_done=self._lib_refresh, track_btn=btn)

    def _music_clear(self):
        crate = self._music_crate()
        if not crate:
            return self.log_line("  \u2717 pick a category first")
        btn = "music-clear"
        if getattr(self, "_armed", None) != btn:
            self._arm(btn, "\u26a0 Confirm \u2014 delete %s" % crate)
            return self.log_line(
                "  \u26a0 This deletes EVERY downloaded track in the '%s' "
                "category locally, then syncs the deletion to AzuraCast so "
                "the two stay in match. Click again to confirm." % crate)
        self._disarm()
        return self.run_cmd(
            self.py("anghami_dl.py", "--clear", crate),
            "Clearing the %s category" % crate,
            on_done=self._music_after_clear)

    def _music_after_clear(self):
        self._lib_refresh()
        self.run_cmd(self.py("library.py", "sync", "music"),
                     "Syncing the music pool with AzuraCast")

    def _save_settings(self):
        updates = {
            "GEMINI_API_KEY": self.query_one("#set-key", Input).value.strip(),
            "GEMINI_TTS_VOICE": str(self.query_one("#set-voice", Select).value),
            "GEMINI_TTS_MODEL":
                self.query_one("#set-tts-model", Input).value.strip(),
            "GEMINI_TEXT_MODEL":
                self.query_one("#set-text-model", Input).value.strip(),
            "GOOGLE_TTS_RPM": self.query_one("#set-rpm", Input).value.strip(),
            "AZURACAST_API_KEY":
                self.query_one("#set-az-key", Input).value.strip(),
        }
        updates = {k: v for k, v in updates.items() if v != ""}
        try:
            set_env_values(updates)
        except Exception as e:  # noqa: BLE001
            return self.log_line("  \u2717 could not save .env: %s" % e)
        self.log_line("  \u2713 saved %d setting(s) to .env" % len(updates))


def main():
    BouzidApp().run()


if __name__ == "__main__":
    main()
