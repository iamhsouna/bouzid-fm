#!/usr/bin/env bash
# =============================================================================
#  Bouzid FM 2.0 — one-line installer (Google-only, TUI-first)
#  Stands up a 24/7 automated Tunisian web/FM radio: AzuraCast, a yt-dlp
#  trending-music fetcher, an auto-built weekly schedule, and the `bouzid`
#  control CLI / TUI. Tested on Ubuntu 24.04 / 26.04.
#
#  Content (text + voice) is generated entirely with Google Gemini — no other
#  provider, no local AI box. All you need is a free GEMINI_API_KEY in .env.
#
#  Your music library lives OUTSIDE the app folder (in your home directory) so
#  removing/reinstalling the app never deletes it and never re-downloads.
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/<you>/bouzid-fm/main/install.sh | bash
#  or, from a clone:
#    ./install.sh
#
#  Updating later: just `git pull` (you do NOT need to re-run this script).
# =============================================================================
set -euo pipefail

REPO_URL="${BOUZID_REPO:-https://github.com/your-username/bouzid-fm.git}"
INSTALL_DIR="${BOUZID_HOME:-$HOME/bouzid-fm}"
BRANCH="${BOUZID_BRANCH:-main}"
# Persistent music library, separate from the app folder (override with $MUSIC_DIR).
DEFAULT_MUSIC_DIR="${MUSIC_DIR:-$HOME/bouzid-music}"

blue()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
die()   { red "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run as a normal user (not root). It will sudo when needed."
command -v sudo >/dev/null || die "sudo is required."

# --- 1) System dependencies -------------------------------------------------
blue "Installing system dependencies ..."
sudo apt-get update -y
# NOTE: no host nginx — AzuraCast serves its web UI from the container, and a
# host nginx would steal port 80 from it.
sudo apt-get install -y ca-certificates curl git jq ffmpeg python3 python3-pip python3-venv python3-full

# yt-dlp: install the maintained standalone binary (more current than apt)
if ! command -v yt-dlp >/dev/null; then
  blue "Installing yt-dlp ..."
  sudo curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
  sudo chmod a+rx /usr/local/bin/yt-dlp
fi

# --- 2) Docker + Compose ----------------------------------------------------
if ! command -v docker >/dev/null; then
  blue "Installing Docker ..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
  green "Added $USER to docker group (re-login may be needed for non-sudo docker)."
fi
docker compose version >/dev/null 2>&1 || sudo apt-get install -y docker-compose-plugin

# --- 3) Get the project -----------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  if git -C "$INSTALL_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    blue "Updating existing checkout in $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" pull --ff-only || true
  fi
elif [ -f "$(dirname "$0")/docker-compose.yml" ]; then
  # running from inside a clone
  INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
  blue "Running from existing checkout: $INSTALL_DIR"
else
  blue "Cloning $REPO_URL ..."
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# Ignore file-mode (chmod +x) changes in this checkout so a later `git pull`
# never aborts with "local changes would be overwritten" just because we mark
# the launcher executable. It is a pure permission bit, not a content change.
git config core.fileMode false 2>/dev/null || true

# --- 4) Environment ---------------------------------------------------------
if [ ! -f .env ]; then
  blue "Creating .env from template ..."
  cp .env.example .env
fi
set -a; . ./.env; set +a

if [ -z "${GEMINI_API_KEY:-}" ]; then
  red "Heads up: GEMINI_API_KEY is empty in .env."
  red "Get a free key at https://aistudio.google.com/apikey and put it in .env"
  red "before generating any news / shows (the stack still installs fine now)."
fi

# --- 4a) Persistent music library (outside the app folder) ------------------
# Pick a music dir that survives reinstalls. Use the existing one from .env
# only if it is an absolute path OUTSIDE the app folder; otherwise relocate to
# the home-directory default so wiping the app never deletes the library.
CUR_MD="${MUSIC_DIR:-}"
NEED_SET=1
case "$CUR_MD" in
  /*) case "$CUR_MD" in "$INSTALL_DIR"/*) NEED_SET=1;; *) NEED_SET=0;; esac;;
esac
if [ "$NEED_SET" = 1 ]; then MUSIC_DIR="$DEFAULT_MUSIC_DIR"; else MUSIC_DIR="$CUR_MD"; fi

# Persist the resolved absolute path into .env (Docker needs an absolute path).
if grep -q '^MUSIC_DIR=' .env; then
  sed -i "s|^MUSIC_DIR=.*|MUSIC_DIR=${MUSIC_DIR}|" .env
else
  printf '\nMUSIC_DIR=%s\n' "$MUSIC_DIR" >> .env
fi
export MUSIC_DIR
mkdir -p "$MUSIC_DIR"

# One-time migration: if an older in-repo ./music has tracks, move them to the
# persistent library so we never re-download what you already have.
LEGACY_MUSIC="$INSTALL_DIR/music"
if [ -d "$LEGACY_MUSIC" ] && [ "$LEGACY_MUSIC" != "$MUSIC_DIR" ] \
   && find "$LEGACY_MUSIC" -type f -print -quit 2>/dev/null | grep -q .; then
  blue "Migrating existing music from $LEGACY_MUSIC to $MUSIC_DIR ..."
  cp -an "$LEGACY_MUSIC/." "$MUSIC_DIR/" 2>/dev/null || true
  rm -rf "$LEGACY_MUSIC" 2>/dev/null || sudo rm -rf "$LEGACY_MUSIC" 2>/dev/null || true
  green "Music library now lives at $MUSIC_DIR (safe across reinstalls)."
fi

blue "Creating music library folders under ${MUSIC_DIR} ..."
for d in morning daytime afternoon evening night overnight friday weekend jingles news; do
  mkdir -p "${MUSIC_DIR}/${d}"
done
mkdir -p data voices
# Station imaging folders (the mastered SFX ship in the release; this is just a
# safety net so the mixer always has somewhere to look).
for d in intro outro beds transition; do mkdir -p "assets/${d}"; done

# --- 4b) Control panel (TUI) deps + generated artifacts --------------------
blue "Setting up the control-panel (TUI) virtualenv (.venv) ..."
if python3 -m venv --system-site-packages .venv 2>/dev/null; then
  .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
  if .venv/bin/pip install -q -r requirements.txt; then
    green "TUI deps installed in .venv (bouzid tui uses it automatically)."
  else
    red "venv pip install failed; run later: .venv/bin/pip install -r requirements.txt"
  fi
else
  red "Could not create .venv (is python3-venv installed?). Trying --user fallback ..."
  pip3 install --user -q --break-system-packages -r requirements.txt 2>/dev/null \
    || red "pip install failed; run later: pip3 install --break-system-packages -r requirements.txt"
fi

# --- 5) Install the bouzid CLI ---------------------------------------------
blue "Installing 'bouzid' CLI to /usr/local/bin ..."
# Install the launcher as a tiny WRAPPER script (NOT a symlink). A symlink to
# bin/bouzid only works while that file keeps its executable bit — but git
# stores it non-executable, so every `git pull` that changes bin/bouzid strips
# the bit and `bouzid` dies with "Permission denied". The wrapper runs the repo
# script through `bash`, so the launcher never depends on that bit again.
chmod +x install.sh 2>/dev/null || true
sudo tee /usr/local/bin/bouzid >/dev/null <<EOF
#!/usr/bin/env bash
exec bash "${INSTALL_DIR}/bin/bouzid" "\$@"
EOF
sudo chmod +x /usr/local/bin/bouzid

# --- 6) Launch the stack ----------------------------------------------------
# free port 80 if a host web server grabbed it (e.g. nginx from a prior run)
if systemctl is-active --quiet nginx 2>/dev/null; then
  red "Host nginx is using port 80 — disabling it so AzuraCast can bind :80"
  sudo systemctl disable --now nginx || true
fi

blue "Starting the radio stack (AzuraCast) ..."
docker compose pull || red "Some images failed to pull; check the output above."
docker compose up -d

# AzuraCast builds its database on first boot (~5-10 min) inside the container.
# There is no reason to block the installer on it — finish now, open the UI when ready.
green "AzuraCast container started — it is initializing in the background."
blue  "  First boot takes ~5-10 min. You do NOT need to wait here."
blue  "  Open http://localhost when ready  •  check anytime with: bouzid health"

# --- 7) Weekly grid (fast, presentation only) ------------------------------
blue "Building the weekly grid ..."
python3 scripts/build_grid.py || true

# --- 8) systemd timer: refresh trending music every FETCH_INTERVAL ---------
# (Done BEFORE the first music download so all sudo steps happen up front,
#  while your sudo credentials are still cached.)
blue "Installing systemd timer for periodic music refresh ..."
UNIT_DIR=/etc/systemd/system
sudo tee ${UNIT_DIR}/bouzid-fetch.service >/dev/null <<EOF
[Unit]
Description=Bouzid FM - fetch trending Tunisian music
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/scripts/fetch_trending.py --quiet
User=${USER}
EOF
sudo tee ${UNIT_DIR}/bouzid-fetch.timer >/dev/null <<EOF
[Unit]
Description=Run Bouzid FM music fetch every ${FETCH_INTERVAL:-6h}

[Timer]
OnBootSec=10min
OnUnitActiveSec=${FETCH_INTERVAL:-6h}
Persistent=true

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bouzid-fetch.timer || red "Could not enable timer (no systemd?)."

# --- 8b) Kick off the FIRST music download in the BACKGROUND ----------------
# Only if the library is empty — if you migrated/kept music, we skip re-downloading.
if find "$MUSIC_DIR" -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.opus' \) -print -quit 2>/dev/null | grep -q .; then
  green "Music library already populated at $MUSIC_DIR — skipping the initial download."
  blue  "  Grab fresh trending tracks any time with:  bouzid fetch --bg"
else
  blue "Starting the first music download in the background (this can take a while) ..."
  if sudo systemctl start --no-block bouzid-fetch.service 2>/dev/null; then
    green "Music is downloading in the background (managed by systemd)."
  else
    nohup python3 scripts/fetch_trending.py --quiet > data/initial-fetch.log 2>&1 &
    green "Music is downloading in the background (PID $!)."
  fi
  blue  "  Check progress any time with:   bouzid fetch-status"
  blue  "  Live view:                      bouzid fetch-status --watch"
fi

# --- 9) Done ----------------------------------------------------------------
green ""
green "========================================================"
green "  Bouzid FM 2.0 is installed."
green "========================================================"
blue "AzuraCast one-time setup (do this once):"
echo "  1. Open http://localhost and finish the guided setup (create the admin)."
echo "     First boot can take 5-10 min; if it doesn't load yet, wait and retry."
echo "  2. Create a station with URL Stub / shortcode: bouzid"
echo "  3. My Account > API Keys > create a key; put it in .env as AZURACAST_API_KEY"
echo "  4. Then run:  bouzid rescan     # imports your music + builds the weekly playlists"
green ""
bouzid urls || true
cat <<EOF

Next steps:
  1. bouzid health                         # confirm all services are up
  2. Put your free Google key in .env:     GEMINI_API_KEY=...   (aistudio.google.com/apikey)
  3. Try it:  bouzid say "مرحبا بكم"   then   bouzid news
   4. Tune music sources any time in config/crates.json, then: bouzid fetch --bg

Updating later:
  • Just `git pull` in this folder — no need to re-run install.sh.

Your music library: ${MUSIC_DIR}
  • It lives OUTSIDE this app folder, so it survives reinstalls.
  • 'bouzid uninstall' KEEPS it; use 'bouzid uninstall --purge-music' to delete it.

The control panel IS the app:        bouzid tui
Manage everything from the CLI with: bouzid help
EOF
