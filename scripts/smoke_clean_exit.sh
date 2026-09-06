#!/usr/bin/env bash
# Process-tree clean-exit smoke.
# Verifies SIGTERM on the backend joins the runtime worker without an
# orphan "lost its parent process" line. Does not launch the Tauri window.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
uv run pytest -q tests/test_clean_exit.py tests/test_health_ops_ui.py
