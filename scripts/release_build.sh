#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(cat VERSION 2>/dev/null | tr -d ' \n\r' || echo 0.1.0)"
OUT_DIR="${1:-/data}"
NAME="bouzid-fm-release-${VERSION}"

TMP="$(mktemp -d)"
DEST="$TMP/$NAME"
mkdir -p "$DEST"

# Copy only what a production release needs.
# Note: rsync may not be installed in all environments, so we use tar.
tar \
  --exclude='./.git' \
  --exclude='./**/__pycache__' \
  --exclude='./data' \
  --exclude='./data/**' \
  --exclude='./.env' \
  --exclude='./**/*.log' \
  -cf - . \
  | tar -xf - -C "$DEST"

# Make sure scripts are executable
chmod +x "$DEST/install.sh" "$DEST/bin/bouzid" || true
chmod +x "$DEST/scripts"/*.sh 2>/dev/null || true

# Deterministic-ish zip (same file order)
(
  cd "$TMP"
  zip -qr "$OUT_DIR/${NAME}.zip" "$NAME"
)

# Checksums
(
  cd "$OUT_DIR"
  sha256sum "${NAME}.zip" > "${NAME}.zip.sha256"
)

echo "Wrote: $OUT_DIR/${NAME}.zip"
echo "Wrote: $OUT_DIR/${NAME}.zip.sha256"
