#!/usr/bin/env bash
# Launch BossMod AI desktop app.
# First run will compile the Rust shell (~1-2 min), subsequent runs are instant.

set -e
cd "$(dirname "$0")"

# Ensure Python deps are installed
uv sync --quiet

# Build the Tauri desktop shell
cd desktop
cargo build --release --quiet

# Run from project root so the backend finds main.py
cd ..
./desktop/target/release/bossmod-desktop
