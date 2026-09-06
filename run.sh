#!/usr/bin/env bash
# Launch BossMod AI desktop app.
# First run compiles the Rust shell (~1-2 min), subsequent runs are instant.

set -e
cd "$(dirname "$0")"

# Ensure Python deps are installed
uv sync --quiet

BINARY="desktop/target/release/bossmod-desktop"

# Only rebuild if binary is missing or source files are newer
if [ ! -f "$BINARY" ] || \
   [ "desktop/src/main.rs" -nt "$BINARY" ] || \
   [ "desktop/Cargo.toml" -nt "$BINARY" ] || \
   [ "desktop/tauri.conf.json" -nt "$BINARY" ] || \
   [ "desktop/icons/icon.png" -nt "$BINARY" ] || \
   [ "desktop/icons/icon.ico" -nt "$BINARY" ] || \
   [ "desktop/icons/icon.icns" -nt "$BINARY" ]; then
    echo "[BossMod] Rebuilding desktop shell — this may take a minute..."
    cd desktop
    cargo build --release
    cd ..
else
    echo "[BossMod] Desktop shell up to date"
fi

exec ./"$BINARY"
