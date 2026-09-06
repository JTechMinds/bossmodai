#!/usr/bin/env bash
# Launch BossMod AI desktop app.
# First run compiles the Rust shell (~1-2 min), subsequent runs are instant.
#
# This script stays in the foreground so one Ctrl+C returns to the shell.
# The desktop is placed in its own process group; quit is SIGTERM so the
# desktop can run ordered backend/worker teardown (shutdown_runtime first).

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

desktop_pid=""

stop_desktop() {
    if [ -n "$desktop_pid" ] && kill -0 "$desktop_pid" 2>/dev/null; then
        kill -TERM "$desktop_pid" 2>/dev/null || true
        i=0
        while [ "$i" -lt 80 ]; do
            kill -0 "$desktop_pid" 2>/dev/null || break
            sleep 0.1
            i=$((i + 1))
        done
        if kill -0 "$desktop_pid" 2>/dev/null; then
            kill -KILL "$desktop_pid" 2>/dev/null || true
        fi
        wait "$desktop_pid" 2>/dev/null || true
    fi
}

sweep_recorded_backend() {
    # Backstop only: signal the PID the desktop recorded.
    pidfile=".bossmod-backend.pid"
    [ -f "$pidfile" ] || return 0
    pid=$(tr -d '[:space:]' < "$pidfile" || true)
    case "$pid" in
        ''|*[!0-9]*) return 0 ;;
    esac
    if [ -r "/proc/${pid}/cmdline" ] && grep -a -F -q "$(pwd)/main.py" "/proc/${pid}/cmdline"; then
        kill -TERM "$pid" 2>/dev/null || true
    fi
}

trap 'stop_desktop; sweep_recorded_backend; exit 130' INT
trap 'stop_desktop; sweep_recorded_backend; exit 143' TERM

# Own process group so the terminal SIGINT lands on this script, not the tree.
.venv/bin/python -c 'import os, sys; os.setpgid(0, 0); os.execvp(sys.argv[1], sys.argv[1:])' ./"$BINARY" &
desktop_pid=$!
set +e
wait "$desktop_pid"
status=$?
set -e
trap - INT TERM
sweep_recorded_backend
exit "$status"
