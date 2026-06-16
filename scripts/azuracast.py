#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM — AzuraCast API client + CLI.

A thin, dependency-free wrapper over the AzuraCast REST API, used by the
control panel (TUI) and the news pipeline. Talks to a self-hosted AzuraCast
(see docker-compose.yml).

Auth: an API key from AzuraCast > My Account > API Keys, sent as `X-API-Key`.

Env:
    AZURACAST_URL        e.g. http://localhost   (default)
    AZURACAST_API_KEY    your API key
    AZURACAST_STATION    station id or shortcode  (default: bouzid)

Examples:
    python3 scripts/azuracast.py nowplaying
    python3 scripts/azuracast.py files
    python3 scripts/azuracast.py upload /path/bulletin.mp3 --to News/bulletin.mp3
    python3 scripts/azuracast.py playlists
    python3 scripts/azuracast.py skip
"""
import argparse
import base64
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request

from library import show_dir

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _env(k, d=""):
    return os.environ.get(k, d)


def _load_schedule_cfg():
    """Load config/schedule_config.py (the single source of truth for the grid)."""
    path = os.path.join(ROOT, "config", "schedule_config.py")
    spec = importlib.util.spec_from_file_location("schedule_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hhmm(s):
    """'09:00' -> 900, '13:30' -> 1330, '24:00' -> 2359 (AzuraCast end-of-day)."""
    h, m = s.split(":")
    v = int(h) * 100 + int(m)
    return 2359 if v >= 2400 else v


def _show_folder(slug):
    """Slug-relative folder under music root (e.g. 'news', 'cook')."""
    sd = show_dir(slug)
    md = _music_dir()
    if sd.startswith(md):
        return os.path.relpath(sd, md)
    return slug


# Audio extensions recognised everywhere (matches AzuraCast.AUDIO_EXTS).
_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
               ".flac", ".wav", ".wma")


def _music_dir():
    """Resolve the local music library root (MUSIC_DIR env, else <repo>/music)."""
    md = os.environ.get("MUSIC_DIR", os.path.join(ROOT, "music"))
    md = os.path.expanduser(os.path.expandvars(md))
    return md if os.path.isabs(md) else os.path.join(ROOT, md)


def discover_music_crates(music_dir=None):
    """Return sorted folder names under `music_dir` that hold >=1 audio file.

    A folder is a "music crate" iff some file beneath it (recursively) has an
    audio extension. The news/ and jingles/ asset folders are excluded because
    they are scheduled as their own news/jingle playlists, not as music
    rotation. show/asset folders (cook, sidibouzid, farmer, ...) are naturally
    ignored: they're never referenced by schedule_config.PLAYLISTS, so even if
    they contain audio they won't be promoted into a music daypart.

    This is the single source of truth for "which folders have music right now"
    — the weekly scheduler and music-reset both call it so empty dayparts are
    auto-skipped and re-activate the moment you drop music into a folder.
    """
    if music_dir is None:
        music_dir = _music_dir()
    try:
        cfg = _load_schedule_cfg()
        excluded = {getattr(cfg, "NEWS_FOLDER", "news").lower(),
                    getattr(cfg, "JINGLE_FOLDER", "jingles").lower()}
    except Exception:  # noqa: BLE001
        excluded = {"news", "jingles"}
    crates = set()
    if not os.path.isdir(music_dir):
        return []
    for entry in sorted(os.listdir(music_dir)):
        full = os.path.join(music_dir, entry)
        if not os.path.isdir(full) or entry.lower() in excluded:
            continue
        for _base, _dirs, names in os.walk(full):
            if any(n.lower().endswith(_AUDIO_EXTS) and not n.startswith(".")
                   for n in names):
                crates.add(entry)
                break
    return sorted(crates)


class AzuraCast:
    def __init__(self, url=None, api_key=None, station=None):
        self.url = (url or _env("AZURACAST_URL", "http://localhost")).rstrip("/")
        # Hardcoded fallback so Skip / Air / upload work out-of-the-box even
        # before the operator sets their own key (.env or Settings override it).
        self.api_key = api_key or _env(
            "AZURACAST_API_KEY",
            "f9cf1ebc9e90e511:55b5f72fe1c735eabc20c0270d65a453")
        self.station = str(station or _env("AZURACAST_STATION", "bouzid"))

    def _req(self, method, path, data=None, auth=True, timeout=60, retries=3):
        u = self.url + path
        body = None
        headers = {"Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth and self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(u, data=body, method=method, headers=headers)

        last_err = None
        for attempt in range(max(1, int(retries))):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                # Retry on transient 5xx; fail fast on 4xx.
                msg = e.read().decode("utf-8", "replace")
                if 500 <= int(getattr(e, "code", 0)) < 600 and attempt < retries - 1:
                    time.sleep(0.6 * (attempt + 1))
                    last_err = RuntimeError(
                        "AzuraCast %s %s -> HTTP %s: %s"
                        % (method, path, e.code, msg[:200]))
                    continue
                raise RuntimeError("AzuraCast %s %s -> HTTP %s: %s"
                                   % (method, path, e.code, msg[:400]))
            except urllib.error.URLError as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                raise RuntimeError("Cannot reach AzuraCast at %s (%s)" % (u, e))
        raise RuntimeError("AzuraCast request failed (%s)" % last_err)

    # ---- read ----
    def now_playing(self):
        return self._req("GET", "/api/nowplaying/%s" % self.station, auth=False)

    def stations(self):
        return self._req("GET", "/api/stations", auth=False)

    def files(self):
        return self._req("GET", "/api/station/%s/files" % self.station)

    def playlists(self):
        return self._req("GET", "/api/station/%s/playlists" % self.station)

    def requestable(self):
        return self._req("GET", "/api/station/%s/requests" % self.station, auth=False)

    # ---- write ----
    def upload(self, local_path, dest_path=None):
        dest = dest_path or os.path.basename(local_path)
        with open(local_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return self._req("POST", "/api/station/%s/files" % self.station,
                         data={"path": dest, "file": b64})

    def create_playlist(self, name, **kw):
        payload = {"name": name}
        payload.update(kw)
        return self._req("POST", "/api/station/%s/playlists" % self.station,
                         data=payload)

    def skip(self):
        return self._req("POST", "/api/station/%s/backend/skip" % self.station,
                         data={})

    def previous(self, back=1):
        """Cut the previously-played track onto the air IMMEDIATELY.

        AzuraCast has no native "previous" endpoint, so we reconstruct it:
        read ``now_playing()["song_history"]`` (most-recent-first), take the
        Nth-most-recent entry, resolve its ``song_id`` to a media-library path,
        and push it onto the interrupting queue with a skip -- the exact same
        push+skip mechanism ``play_now`` uses (so it cuts in within ~1-3s and
        works repeatedly).

        ``back`` (default 1) = how many tracks back to jump: 1 = the song that
        played right before the current one, 2 = the one before that, etc.

        The history entry's ``song.id`` is cross-referenced against each media
        file's ``song_id`` (both are the same MD5-ish song hash), which is far
        more reliable than fuzzy text matching.
        """
        if back < 1:
            raise RuntimeError("back must be >= 1")
        np = self.now_playing()
        history = np.get("song_history") or []
        if len(history) < back:
            raise RuntimeError(
                "No track that far back in history (only %d entr%s available)."
                % (len(history), "y" if len(history) == 1 else "ies"))
        entry = history[back - 1]
        song = entry.get("song") or {}
        song_id = song.get("id")
        title = song.get("text") or song.get("title") or "(unknown)"

        # Resolve the song_id to an on-disk media path. Build a song_id->path
        # map from the station library; this is the authoritative link between
        # a history entry and the file that produced it.
        rel = None
        if song_id:
            for f in self.files():
                if f.get("song_id") == song_id:
                    rel = (f.get("path") or "").lstrip("/")
                    break
        if not rel:
            raise RuntimeError(
                "The previous track (%s) isn't in the media library -- it may "
                "have been a remote stream, a live show, or removed. Only "
                "library tracks can be replayed." % title)

        abs_path = "%s/%s" % (self.media_root(), rel)
        base = os.path.basename(rel)
        print("  replaying previous track on air: %s" % title)
        resp = None
        reply = ""
        try:
            resp = self.telnet("interrupting_requests.push %s" % abs_path)
            reply = self._telnet_response(resp)
            # Advance the interrupting source to the pushed track + drain stale
            # ids, same as play_now -- without this repeat cut-ins never air.
            self.telnet("interrupting_requests.skip")
        except RuntimeError as e:
            resp = {"error": str(e)[:200]}
        accepted = reply.isdigit()
        pulled = self._queue_drained(reply.strip(), timeout=3) if accepted else False
        on_air = pulled or self.wait_until_airing(base, timeout=6)
        if not on_air:
            raise RuntimeError(
                "Pushed the previous track to the backend but could not confirm "
                "it cut on air. Confirm the admin API key has full administrator "
                "permissions.")
        return {"airing": base, "title": title, "media_path": abs_path,
                "queued": accepted, "queued_confirmed": pulled,
                "on_air": on_air, "backend_response": resp}

    def request_song(self, request_id):
        return self._req("POST", "/api/station/%s/request/%s"
                         % (self.station, request_id), data={})

    def queue(self):
        return self._req("GET", "/api/station/%s/queue" % self.station)

    def clear_queue(self):
        """Empty the pre-computed AutoDJ up-next queue so a freshly submitted
        request becomes the very next track the AutoDJ plays. Uses per-item
        deletes (the universally supported endpoint)."""
        try:
            items = self.queue()
        except RuntimeError:
            return 0
        if isinstance(items, dict):
            items = items.get("rows") or items.get("items") or []
        n = 0
        for it in (items or []):
            qid = it.get("id") if isinstance(it, dict) else None
            if qid is None:
                continue
            try:
                self._req("DELETE", "/api/station/%s/queue/%s"
                          % (self.station, qid))
                n += 1
            except RuntimeError:
                pass
        return n

    def station_numeric_id(self):
        """Resolve the station's numeric id (admin endpoints need it)."""
        try:
            np = self.now_playing()
            sid = (np.get("station") or {}).get("id")
            if sid:
                return sid
        except RuntimeError:
            pass
        try:
            for s in self.stations():
                if (str(s.get("shortcode")) == self.station
                        or str(s.get("id")) == self.station):
                    return s.get("id")
        except RuntimeError:
            pass
        return None

    def ensure_requests_enabled(self):
        """Turn on zero-delay song requests via the admin API.

        Reloads the backend once if requests were previously off, since
        Liquidsoap only exposes the request queue when the station config is
        rebuilt. Raises with a clear, actionable message if the admin key is
        not permitted to change station settings.
        """
        sid = self.station_numeric_id()
        if not sid:
            raise RuntimeError(
                "Could not resolve the station id to enable requests. Enable "
                "'Allow Song Requests' once in AzuraCast (Station > Profile).")
        already = False
        try:
            cur = self._req("GET", "/api/admin/station/%s" % sid)
            already = bool(cur.get("enable_requests"))
        except RuntimeError:
            pass
        if already:
            return True
        try:
            self._req("PUT", "/api/admin/station/%s" % sid,
                      data={"enable_requests": True, "request_delay": 0,
                            "request_threshold": 0})
        except RuntimeError as e:
            raise RuntimeError(
                "Could not enable song requests via the admin API (%s). "
                "Enable it once in AzuraCast: Station > Profile > General > "
                "'Allow Song Requests' = Yes, and set Request Delay to 0."
                % str(e)[:160])
        print("  enabling song requests (one-time backend reload) ...")
        try:
            self.restart_broadcasting()
        except RuntimeError:
            pass
        time.sleep(10)
        return True

    def find_file(self, needle):
        needle = needle.lstrip("/").lower()
        for f in self.files():
            if needle in (f.get("path") or "").lower():
                return f
        return None

    def ensure_playlist(self, name, **kw):
        existing = self.playlists_by_name()
        if name in existing:
            return existing[name]
        self.create_playlist(name, **kw)
        return self.playlists_by_name().get(name)

    def find_request_id(self, needle, song_id=None):
        needle = os.path.splitext(os.path.basename(needle))[0].lower()
        for r in self.requestable():
            song = r.get("song") or {}
            if song_id and str(song.get("id")) == str(song_id):
                return r.get("request_id")
            hay = " ".join(str(song.get(k, ""))
                           for k in ("text", "title", "artist", "album")).lower()
            if needle and needle in hay:
                return r.get("request_id")
        return None

    # ---- low-level Liquidsoap / AutoDJ control (admin debug endpoints) ----
    def telnet(self, command):
        """Run a raw Liquidsoap telnet command on the station backend.

        Documented endpoint (confirmed in AzuraCast's routes + TelnetAction,
        which reads the `command` parameter and forwards it to the backend):
            PUT /api/admin/debug/station/<numeric_id>/telnet  {"command": ...}
        """
        sid = self.station_numeric_id()
        if not sid:
            raise RuntimeError(
                "Could not resolve the numeric station id needed for backend "
                "control. Check AZURACAST_STATION and that the API key has "
                "administrator permissions.")
        return self._req("PUT", "/api/admin/debug/station/%s/telnet" % sid,
                         data={"command": command})

    def clear_station_queue(self):
        """Clear + regenerate the upcoming AutoDJ queue.

        Documented endpoint:
            PUT /api/admin/debug/station/<numeric_id>/clearqueue
        """
        sid = self.station_numeric_id()
        if not sid:
            return None
        try:
            return self._req(
                "PUT", "/api/admin/debug/station/%s/clearqueue" % sid)
        except RuntimeError:
            return None

    def station_shortcode(self):
        """Station short name (used for the on-disk media directory)."""
        try:
            np = self.now_playing()
            sc = (np.get("station") or {}).get("shortcode")
            if sc:
                return str(sc)
        except RuntimeError:
            pass
        return self.station

    def media_root(self):
        """On-disk media directory inside the AzuraCast backend container.

        Standard Docker installs keep station media at
        /var/azuracast/stations/<shortcode>/media (per the AzuraCast docs and
        docker volume layout). Override with AZURACAST_MEDIA_ROOT for custom or
        S3 storage locations.
        """
        env = _env("AZURACAST_MEDIA_ROOT", "")
        if env:
            return env.rstrip("/")
        return "/var/azuracast/stations/%s/media" % self.station_shortcode()

    def _np_song_text(self):
        try:
            np = self.now_playing()
        except RuntimeError:
            return ""
        cur = (np.get("now_playing") or {}).get("song") or {}
        return " ".join(str(cur.get(k, ""))
                        for k in ("text", "title", "artist")).lower()

    def wait_until_airing(self, needle, timeout=30):
        """Poll now-playing until the clip identified by `needle` is on air."""
        stem = os.path.splitext(os.path.basename(needle))[0].lower()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stem and stem in self._np_song_text():
                return True
            time.sleep(2)
        return False

    @staticmethod
    def _telnet_response(resp):
        """Pull the backend's telnet reply text out of the admin-API log.

        AzuraCast wraps the raw telnet response in debug log records; the
        Liquidsoap `request.queue.push` command replies with the new request
        id (a bare integer) when the push is accepted.
        """
        try:
            for entry in (resp or {}).get("logs", []) or []:
                ctx = entry.get("context") or {}
                r = ctx.get("response")
                if isinstance(r, list) and r:
                    return str(r[0]).strip()
                if isinstance(r, str) and r.strip():
                    return r.strip()
        except Exception:
            pass
        return ""

    def play_now(self, remote_path, do_skip=True, keep=False, confirm=True):
        """Cut a clip onto the air IMMEDIATELY — true breaking-news behaviour.

        AzuraCast's Liquidsoap configuration defines two request queues:

            requests              = request.queue(id=\"requests\", ...)
            radio = fallback(track_sensitive=true,  [requests, radio])

            interrupting_requests = request.queue(id=\"interrupting_requests\", ...)
            radio = fallback(track_sensitive=false, [interrupting_requests, radio])

        A normal song request lands in `requests`, which is track-sensitive and
        sits behind general rotation, so it only swaps in at the NEXT track
        boundary (this is the \"it just plays the next song\" symptom).

        The `interrupting_requests` queue is track_sensitive=false, so anything
        pushed to it cuts into the current song. BUT a bare push only takes
        effect the FIRST time: Liquidsoap leaves consumed request ids in the
        queue, so the next push piles up behind the stale one and never airs.
        That is exactly why \"Air now\" used to work only once. The fix is to
        follow every push with `interrupting_requests.skip`, which advances the
        interrupting source to the freshly pushed track and drains the stale
        ids. Verified live: push+skip cuts the track in within ~3s, every time.

        `confirm=True` (default) then blocks until the clip is verified on air
        via now-playing — but that feed lags several seconds (AzuraCast updates
        it on a timer, not instantly), so confirmation can take up to ~20s. For
        the TUI's \"Air now\" button this would freeze the button on Cancel long
        after the song already switched; pass `confirm=False` there and run
        confirm_on_air() in a background thread instead.

        `do_skip`/`keep` are kept only for CLI backwards-compatibility.
        """
        base = os.path.basename(remote_path)
        # Wait for the upload to register so we can read its stored path.
        f = None
        for _ in range(15):
            f = self.find_file(remote_path) or self.find_file(base)
            if f:
                break
            time.sleep(2)
        if not f:
            raise RuntimeError("File not visible in AzuraCast yet: %s" % base)
        rel = (f.get("path") or remote_path).lstrip("/")
        abs_path = "%s/%s" % (self.media_root(), rel)
        print("  cutting in on air now (interrupting queue) ...")
        reply = ""
        resp = None
        accepted = False
        try:
            resp = self.telnet("interrupting_requests.push %s" % abs_path)
            reply = self._telnet_response(resp)
            accepted = reply.isdigit()
            # Force the interrupting source to advance to the pushed track and
            # drain any stale request ids left from previous cut-ins. Without
            # this, repeat cut-ins pile up in the queue and never air.
            self.telnet("interrupting_requests.skip")
        except RuntimeError as e:
            resp = {"error": str(e)[:200]}

        # Fast inline confirmation: when the pushed request id vanishes from the
        # interrupting queue, Liquidsoap has pulled the track (it airs within a
        # second or two). This is near-instant and avoids the multi-second lag
        # of the now-playing feed, so callers that need a snappy UI can rely on
        # `queued_confirmed` instead of polling now-playing.
        rid = reply.strip() if accepted else None
        pulled = self._queue_drained(rid, timeout=3) if rid else False

        if confirm:
            confirmed = pulled or self.confirm_on_air(base, abs_path)
            if not confirmed:
                raise RuntimeError(
                    "Pushed the clip to the backend but could not confirm it "
                    "cut on air. Confirm the admin API key has full "
                    "administrator permissions.")
        else:
            confirmed = None  # confirmation deferred to the caller
        return {"airing": base, "method": "interrupting_queue",
                "media_path": abs_path, "queued": accepted,
                "request_id": reply or None, "queued_confirmed": pulled,
                "on_air": confirmed, "confirmed_now_playing": confirmed,
                "backend_response": resp}

    def _queue_drained(self, request_id, timeout=3):
        """Return True once `request_id` is no longer in the interrupting queue.

        Liquidsoap leaves consumed request ids in the queue listing until the
        source advances past them. So once our pushed id disappears, the track
        has been pulled and is airing. This is a much faster "did it take?"
        signal than the now-playing feed (which lags several seconds). Polls
        for up to `timeout` seconds.
        """
        if not request_id:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                listing = self._telnet_response(
                    self.telnet("interrupting_requests.queue")) or ""
            except RuntimeError:
                return False
            if request_id not in listing.split():
                return True
            time.sleep(0.5)
        return False

    def confirm_on_air(self, base, abs_path=None, primary_timeout=20,
                       fallback_timeout=15):
        """Verify the clip `base` actually reached the air, with a fallback.

        Polls now-playing for up to `primary_timeout`; if the interrupting queue
        didn't surface it, retries via the normal request queue + a backend skip
        and polls for `fallback_timeout`. Returns True/False.

        Split out of play_now() so callers that need a snappy UI (the TUI's
        \"Air now\" button) can push+skip synchronously and run this in a
        background thread, instead of freezing the button on Cancel for ~20s
        while now-playing catches up.
        """
        confirmed = self.wait_until_airing(base, timeout=primary_timeout)
        if not confirmed and abs_path:
            try:
                self.telnet("requests.push %s" % abs_path)
                self.skip()
                confirmed = self.wait_until_airing(base, timeout=fallback_timeout)
            except RuntimeError:
                pass
        return confirmed

    def set_file_playlists(self, file_id, playlist_ids):
        return self._req("PUT", "/api/station/%s/file/%s" % (self.station, file_id),
                         data={"playlists": [{"id": p} for p in playlist_ids]})

    def delete_file(self, file_id):
        """Remove a media file from the station library by its numeric id."""
        return self._req("DELETE", "/api/station/%s/file/%s"
                         % (self.station, file_id))

    def rename_file(self, file_id, new_path):
        """Rename / move a media file by editing its library path."""
        return self._req("PUT", "/api/station/%s/file/%s"
                         % (self.station, file_id), data={"path": new_path})

    def delete_playlist(self, pid):
        return self._req("DELETE", "/api/station/%s/playlist/%s"
                         % (self.station, pid))

    def playlists_by_name(self):
        return {p.get("name"): p.get("id") for p in self.playlists()}

    def delete_all_playlists(self, prefix="Bouzid "):
        """Delete every playlist whose name starts with `prefix`.

        Returns the number deleted. Leaves the stock `default` playlist and any
        other non-prefixed playlists untouched. Used by `apply_schedule(reset)`
        and `bouzid flush`.
        """
        n = 0
        for nm, pid in list(self.playlists_by_name().items()):
            if nm and nm.startswith(prefix):
                self.delete_playlist(pid)
                n += 1
        return n

    def delete_all_media(self):
        """Delete every media file from the station library.

        There is no bulk-delete API, so this loops `files()` + `delete_file()`.
        Returns the number deleted. Used by `bouzid flush` for a clean slate.
        """
        files = self.files()
        n = 0
        for f in files:
            fid = f.get("id")
            if fid is None:
                continue
            try:
                self.delete_file(fid)
                n += 1
            except RuntimeError:
                pass  # keep going on per-file failures
        return n

    # ---- weekly scheduler (builds the Bouzid FM weekly grid on AzuraCast) ----
    def build_schedule_plan(self):
        """Translate config/schedule_config.py into AzuraCast playlists.

        Returns a list of (name, payload, source_folders). One scheduled music
        playlist per crate, one news playlist per distinct cadence (top-of-hour
        for 60-min, every-N-minutes otherwise), plus a jingle playlist for the
        dayparts that want a sting before the bulletin.
        """
        cfg = _load_schedule_cfg()
        prefix = "Bouzid "

        windows = {}  # daypart -> [(day_iso, start_hhmm, end_hhmm)]
        for day_key, (_ar, _en, tmpl) in cfg.WEEK.items():
            day_iso = day_key + 1  # AzuraCast: 1=Mon .. 7=Sun
            for (start, end, dp) in tmpl:
                windows.setdefault(dp, []).append((day_iso, _hhmm(start), _hhmm(end)))

        def items_for(dps):
            out = []
            for dp in dps:
                for (d, s, e) in windows.get(dp, []):
                    out.append({"start_time": s, "end_time": e,
                                "days": [d], "loop_once": False})
            return out

        plan = []

        # Which folders actually have music on disk right now. Empty folders are
        # auto-skipped: their daypart playlist isn't created, so that time
        # window falls through to the 24/7 fallback (which plays whatever IS
        # available). Drop music into a folder + run `bouzid rescan` and the
        # daypart re-activates on the next schedule apply. No hand-maintained
        # crate list — it's derived from MUSIC_DIR at runtime.
        live_crates = set(discover_music_crates())

        # 1) Music: group dayparts by the crate/playlist they use, but only for
        #    playlists whose folder is non-empty on disk.
        music_groups = {}
        for dp, info in cfg.DAYPARTS.items():
            if dp in windows:
                folder = (cfg.PLAYLISTS.get(info["playlist"], {})
                          .get("folder", "")).lower()
                if folder in live_crates:
                    music_groups.setdefault(info["playlist"], []).append(dp)
        for pl, dps in music_groups.items():
            folder = cfg.PLAYLISTS[pl]["folder"]
            name = prefix + cfg.PLAYLISTS[pl]["label"]
            plan.append((name, {
                "name": name, "type": "default", "source": "songs",
                "order": "shuffle", "is_enabled": True, "is_jingle": False,
                "weight": 3, "include_in_requests": False,
                "include_in_on_demand": True,
                "schedule_items": items_for(dps),
            }, [folder]))

        # 1b) Always-on fallback (general rotation). This has NO schedule, so
        #     AzuraCast only plays it when no scheduled daypart playlist has
        #     playable media right now. It guarantees the station is NEVER
        #     offline — e.g. if a daypart folder hasn't been filled yet, or in
        #     any gap between windows. Scheduled playlists still take priority
        #     during their time slots. It pulls from every non-empty music
        #     folder (derived from disk, so it survives even if every daypart
        #     is empty — it'll just play whatever has music).
        all_music_folders = sorted(live_crates)
        if all_music_folders:
            fb_name = prefix + "24/7 (All Music)"
            plan.append((fb_name, {
                "name": fb_name, "type": "default", "source": "songs",
                "order": "shuffle", "is_enabled": True, "is_jingle": False,
                "weight": 1, "include_in_requests": False,
                "include_in_on_demand": True,
                # no schedule_items => general rotation, always eligible
            }, all_music_folders))

        # 1c) Scheduled shows (news, cook, sidibouzid, ...). Each show that has
        #     at least one scheduled time window gets its own AzuraCast playlist
        #     pointing to its episode folder. The playlist has a schedule so it
        #     only plays during its window; any time gaps fall through to music.
        _preset_shows = set(getattr(cfg, "SHOWS", {}))
        ordered_shows = sorted(_preset_shows) if _preset_shows else []
        for slug in ordered_shows:
            folder = _show_folder(slug)
            if not folder:
                continue
            items = []
            for entry in getattr(cfg, "SHOWS", {}).get(slug, []):
                items.append({
                    "start_time": _hhmm(entry["start"]),
                    "end_time": _hhmm(entry["end"]),
                    "days": [int(entry["day"]) + 1],
                    "loop_once": True,
                })
            if not items:
                continue
            name = prefix + slug.title()
            plan.append((name, {
                "name": name, "type": "default", "source": "songs",
                "order": "shuffle", "is_enabled": True, "is_jingle": False,
                "weight": 4, "include_in_requests": False,
                "include_in_on_demand": True,
                "schedule_items": items,
            }, [folder]))

        # 2) News: one playlist per distinct cadence.
        cad_groups = {}
        for dp, info in cfg.DAYPARTS.items():
            if dp in windows:
                cad_groups.setdefault(int(info["news_every"]), []).append(dp)
        for cad, dps in sorted(cad_groups.items()):
            name = prefix + "News %dm" % cad
            payload = {
                "name": name, "source": "songs",
                "order": "sequential", "is_enabled": True, "is_jingle": False,
                "weight": 5, "include_in_requests": False,
                "include_in_on_demand": True,
                "schedule_items": items_for(dps),
            }
            # IMPORTANT: the playlist TYPE (not just the minute field) decides
            # whether AzuraCast interleaves the bulletin into rotation or plays
            # it as a continuous block. With type "default" + a schedule window
            # the news would loop for the whole daypart and music would never
            # come back, so we use the proper cadence types here.
            if cad == 60:
                payload["type"] = "once_per_hour"
                payload["play_per_hour_minute"] = 0   # top of every hour
            else:
                payload["type"] = "once_per_x_minutes"
                payload["play_per_minutes"] = cad
            plan.append((name, payload, [cfg.NEWS_FOLDER]))

        # 3) Jingle: a voiced station ID injected BETWEEN songs, around the
        #    clock. is_jingle=True => AzuraCast writes no now-playing metadata
        #    for it (true station-ID behaviour); play_per_songs => one jingle
        #    is dropped in after every N normal songs. No schedule_items, so it
        #    interleaves 24/7 rather than only before the news.
        jname = prefix + "Jingle"
        every = int(getattr(cfg, "JINGLE_EVERY_SONGS", 4)) or 4
        plan.append((jname, {
            "name": jname, "type": "once_per_x_songs", "source": "songs",
            "order": "shuffle", "is_enabled": True, "is_jingle": True,
            "weight": 1, "include_in_requests": False,
            "include_in_on_demand": False,
            "play_per_songs": every,
        }, [cfg.JINGLE_FOLDER]))

        return plan

    def apply_schedule(self, dry_run=False, reset=False, assign=True):
        plan = self.build_schedule_plan()

        if dry_run:
            print("AzuraCast schedule plan (station: %s) — %d playlists\n"
                  % (self.station, len(plan)))
            for name, payload, folders in plan:
                bits = []
                if "play_per_hour_minute" in payload:
                    bits.append("at :%02d each hour" % payload["play_per_hour_minute"])
                if "play_per_minutes" in payload:
                    bits.append("every %d min" % payload["play_per_minutes"])
                if "play_per_songs" in payload:
                    bits.append("every %d songs" % payload["play_per_songs"])
                if payload.get("is_jingle"):
                    bits.append("jingle")
                print("  %-34s src=%-10s windows=%-3d %s"
                      % (name, "/".join(folders),
                         len(payload.get("schedule_items", [])),
                         "(" + ", ".join(bits) + ")" if bits else ""))
            print("\n(dry-run — nothing was sent to AzuraCast)")
            return {"planned": len(plan), "dry_run": True}

        if reset:
            n = self.delete_all_playlists()
            print("  deleted %d old 'Bouzid ' playlist(s)" % n)

        for name, payload, _folders in plan:
            self._req("POST", "/api/station/%s/playlists" % self.station,
                      data=payload)
            print("  created playlist:", name)

        assigned = 0
        if assign:
            name_to_id = self.playlists_by_name()
            folder_to_names = {}
            for name, _payload, folders in plan:
                for f in folders:
                    folder_to_names.setdefault(f.lower(), set()).add(name)
            for fobj in self.files():
                path = (fobj.get("path") or "")
                top = path.split("/")[0].lower() if "/" in path else ""
                names = folder_to_names.get(top)
                if not names:
                    continue
                ids = [name_to_id[n] for n in names if n in name_to_id]
                if ids:
                    self.set_file_playlists(fobj.get("id"), ids)
                    assigned += 1
            print("  assigned %d media files to playlists" % assigned)

        print("Schedule applied: %d playlists on station '%s'."
              % (len(plan), self.station))
        return {"playlists": len(plan), "assigned": assigned}

    # ---- media import + broadcasting -------------------------------------
    AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
                  ".flac", ".wav", ".wma")

    def restart_broadcasting(self):
        """Restart the station frontend+backend so new media goes live."""
        return self._req("POST", "/api/station/%s/restart" % self.station,
                         data={})

    def import_dir(self, music_dir, verbose=True):
        """Upload every audio file under music_dir into the AzuraCast library.

        Files keep their crate sub-folder (e.g. ``daytime/song.mp3``) so the
        weekly scheduler can assign them to the matching playlist by folder.
        Already-present paths are skipped, so this is safe to re-run.
        """
        music_dir = os.path.abspath(music_dir)
        if not os.path.isdir(music_dir):
            raise RuntimeError("Music folder not found: %s" % music_dir)

        existing = set()
        try:
            for f in self.files():
                p = f.get("path")
                if p:
                    existing.add(p.lstrip("/"))
        except RuntimeError:
            pass  # empty library / station still warming up

        files = []
        for base, _dirs, names in os.walk(music_dir):
            for n in names:
                if n.lower().endswith(self.AUDIO_EXTS):
                    files.append(os.path.join(base, n))
        files.sort()
        total = len(files)
        if total == 0:
            print("No audio files found under %s (has the download finished? "
                  "check: bouzid fetch-status)" % music_dir)
            return {"uploaded": 0, "skipped": 0, "failed": 0, "total": 0}

        uploaded = skipped = failed = 0
        for i, local in enumerate(files, 1):
            rel = os.path.relpath(local, music_dir).replace(os.sep, "/")
            if rel in existing:
                skipped += 1
                continue
            try:
                self.upload(local, rel)
                uploaded += 1
                if verbose:
                    print("  [%d/%d] uploaded %s" % (i, total, rel))
            except RuntimeError as e:
                failed += 1
                if verbose:
                    print("  [%d/%d] FAILED   %s  (%s)"
                          % (i, total, rel, str(e)[:140]))
        print("Imported into AzuraCast: %d uploaded, %d already present, "
              "%d failed (of %d audio files)."
              % (uploaded, skipped, failed, total))
        return {"uploaded": uploaded, "skipped": skipped,
                "failed": failed, "total": total}

    def sync_dir(self, local_dir, dest_prefix, delete_orphans=True,
                 verbose=True):
        """Make the AzuraCast folder <dest_prefix> match a local folder.

        * Uploads local files missing remotely (matched by basename).
        * If delete_orphans, removes remote files under <dest_prefix> that have
          no local counterpart, so local + AzuraCast stay in match.

        Only the single folder <dest_prefix> is ever touched; files in other
        folders are never removed.
        """
        dest_prefix = (dest_prefix or "").strip("/")
        local_dir = os.path.abspath(local_dir)
        local = {}
        if os.path.isdir(local_dir):
            for fn in os.listdir(local_dir):
                p = os.path.join(local_dir, fn)
                if (os.path.isfile(p)
                        and fn.lower().endswith(self.AUDIO_EXTS)
                        and not fn.startswith(".")):
                    local[fn] = p
        remote = {}
        try:
            for f in self.files():
                rp = (f.get("path") or "").lstrip("/")
                folder = rp.rsplit("/", 1)[0] if "/" in rp else ""
                if folder == dest_prefix:
                    remote[os.path.basename(rp)] = f
        except RuntimeError:
            pass
        uploaded = deleted = failed = 0
        for fn, p in sorted(local.items()):
            if fn in remote:
                continue
            try:
                self.upload(p, (dest_prefix + "/" + fn) if dest_prefix else fn)
                uploaded += 1
                if verbose:
                    print("  \u2191 uploaded %s/%s" % (dest_prefix, fn))
            except Exception as e:  # noqa: BLE001
                failed += 1
                if verbose:
                    print("  \u2717 upload failed %s (%s)"
                          % (fn, str(e)[:120]))
        if delete_orphans:
            for fn, f in sorted(remote.items()):
                if fn in local:
                    continue
                fid = f.get("id")
                if fid is None:
                    continue
                try:
                    self.delete_file(fid)
                    deleted += 1
                    if verbose:
                        print("  \u2717 removed remote orphan %s/%s"
                              % (dest_prefix, fn))
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    if verbose:
                        print("  \u26a0 could not remove %s (%s)"
                              % (fn, str(e)[:120]))
        print("Sync %s: %d uploaded, %d removed, %d failed (%d local, "
              "%d remote)." % (dest_prefix or "/", uploaded, deleted, failed,
                               len(local), len(remote)))
        return {"uploaded": uploaded, "deleted": deleted, "failed": failed,
                "local": len(local), "remote": len(remote)}


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="AzuraCast API client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("nowplaying")
    sub.add_parser("stations")
    sub.add_parser("files")
    sub.add_parser("playlists")
    up = sub.add_parser("upload")
    up.add_argument("file")
    up.add_argument("--to", default=None)
    cp = sub.add_parser("create-playlist")
    cp.add_argument("name")
    sub.add_parser("skip")
    pv = sub.add_parser("previous",
                        help="replay the previously-played track on air now")
    pv.add_argument("--back", type=int, default=1,
                    help="how many tracks back (default 1 = last played)")
    pn = sub.add_parser("play-now",
                        help="request a media file + skip so it airs now")
    pn.add_argument("path", help="AzuraCast media path, e.g. Breaking/x.mp3")
    pn.add_argument("--no-skip", action="store_true",
                    help="queue it as the next track without skipping")
    pn.add_argument("--keep", action="store_true",
                    help="leave it in the requestable playlist afterwards")
    im = sub.add_parser("import",
                        help="upload the music library into AzuraCast")
    im.add_argument("--dir",
                    default=os.environ.get("MUSIC_DIR")
                    or os.path.join(ROOT, "music"),
                    help="music folder to import (default: $MUSIC_DIR)")
    sub.add_parser("restart",
                   help="restart broadcasting so new media/playlists go live")
    sc = sub.add_parser("apply-schedule",
                        help="build the weekly grid as AzuraCast playlists")
    sc.add_argument("--dry-run", action="store_true",
                    help="preview the plan without calling AzuraCast")
    sc.add_argument("--reset", action="store_true",
                    help="delete existing 'Bouzid ' playlists first")
    sc.add_argument("--no-assign", action="store_true",
                    help="do not (re)assign media files to playlists")
    fl = sub.add_parser("flush",
                        help="wipe all media + 'Bouzid ' playlists (clean slate)")
    fl.add_argument("--dry-run", action="store_true",
                    help="show what would be deleted, change nothing")
    fl.add_argument("--media-only", action="store_true",
                    help="only delete media files, keep playlists")
    fl.add_argument("--playlists-only", action="store_true",
                    help="only delete 'Bouzid ' playlists, keep media")
    args = ap.parse_args()

    ac = AzuraCast()
    try:
        if args.cmd == "nowplaying":
            np = ac.now_playing()
            st = np.get("station", {}) if isinstance(np, dict) else {}
            cur = np.get("now_playing", {}) if isinstance(np, dict) else {}
            song = cur.get("song", {}) if isinstance(cur, dict) else {}
            print("Station  :", st.get("name"))
            print("Now      :", song.get("text") or "-")
            print("Listeners:", np.get("listeners", {}).get("current"))
        elif args.cmd == "stations":
            _print(ac.stations())
        elif args.cmd == "files":
            fs = ac.files()
            for f in fs:
                print(f.get("path"))
            print("(%d files)" % len(fs))
        elif args.cmd == "playlists":
            for p in ac.playlists():
                print(p.get("id"), p.get("name"))
        elif args.cmd == "upload":
            _print(ac.upload(args.file, args.to))
        elif args.cmd == "create-playlist":
            _print(ac.create_playlist(args.name))
        elif args.cmd == "skip":
            _print(ac.skip())
        elif args.cmd == "previous":
            _print(ac.previous(back=args.back))
        elif args.cmd == "play-now":
            res = ac.play_now(args.path, do_skip=not args.no_skip,
                              keep=args.keep)
            _print(res)
            if not res.get("on_air"):
                print("  WARNING: the station backend did not accept the clip "
                      "onto the interrupting queue (no request id returned). "
                      "Check that media_path above exists inside the AzuraCast "
                      "backend container, or set AZURACAST_MEDIA_ROOT.",
                      file=sys.stderr)
                sys.exit(2)
        elif args.cmd == "import":
            ac.import_dir(args.dir)
        elif args.cmd == "restart":
            _print(ac.restart_broadcasting())
        elif args.cmd == "apply-schedule":
            ac.apply_schedule(dry_run=args.dry_run, reset=args.reset,
                              assign=not args.no_assign)
        elif args.cmd == "flush":
            do_media = not args.playlists_only
            do_playlists = not args.media_only
            if args.dry_run:
                if do_media:
                    from collections import Counter
                    fs = ac.files()
                    folders = Counter()
                    for f in fs:
                        p = (f.get("path") or "")
                        top = p.split("/")[0] if "/" in p else "(root)"
                        folders[top] += 1
                    print("MEDIA that would be deleted: %d file(s)" % len(fs))
                    for folder, n in sorted(folders.items()):
                        print("  %-20s %d" % (folder, n))
                if do_playlists:
                    pls = [p.get("name") for p in ac.playlists()
                           if (p.get("name") or "").startswith("Bouzid ")]
                    print("\nPLAYLISTS that would be deleted: %d" % len(pls))
                    for nm in pls:
                        print("  -", nm)
                print("\n(dry-run — nothing was deleted)")
            else:
                md = pl = 0
                if do_playlists:
                    md = ac.delete_all_playlists()
                    print("Deleted %d 'Bouzid ' playlist(s)." % md)
                if do_media:
                    pl = ac.delete_all_media()
                    print("Deleted %d media file(s)." % pl)
                print("Flush complete. Rebuild with: bouzid rescan")
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
