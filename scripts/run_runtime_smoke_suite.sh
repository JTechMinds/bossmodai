#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Honest critical-path slice (HA-TEST-P1-01). Historical node-id selectors
# pointed at a missing tests/test_agent_runtime.py; this module is real.
uv run pytest -q tests/test_agent_runtime.py
