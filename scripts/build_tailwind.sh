#!/usr/bin/env bash
# Regenerate ui/static/css/tailwind.generated.css from the app's markup.
#
# The app never compiles CSS at runtime: it links the committed output of this
# script. Run it after adding or changing a Tailwind utility class anywhere in
# ui/static/js or ui/templates; tests/test_tailwind_generated.py fails until
# the output covers every class the source names.
#
# Tool: the Tailwind standalone CLI, pinned to v3.4.17 — the version of the
# Play runtime this replaced, so the output is the CSS that runtime produced.
# It is a dev-time binary only: never committed, never shipped, never loaded
# by the app. Download the release asset `tailwindcss-linux-x64` (or the one
# for your platform) from
#   https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17
# verify it against that release's sha256sums.txt, and point TAILWIND_BIN at it:
#   TAILWIND_BIN=/path/to/tailwindcss-linux-x64 scripts/build_tailwind.sh
set -euo pipefail

readonly PINNED_VERSION="v3.4.17"

if [[ -z "${TAILWIND_BIN:-}" ]]; then
    echo "build_tailwind.sh: TAILWIND_BIN is not set." >&2
    echo "  Point it at the Tailwind standalone CLI ${PINNED_VERSION} binary" >&2
    echo "  (release asset tailwindcss-linux-x64, tailwindlabs/tailwindcss)." >&2
    exit 2
fi
if [[ ! -x "${TAILWIND_BIN}" ]]; then
    echo "build_tailwind.sh: TAILWIND_BIN=${TAILWIND_BIN} is not an executable file." >&2
    exit 2
fi

# A different CLI version emits different CSS; refuse it rather than commit a
# silently different stylesheet.
version_line="$("${TAILWIND_BIN}" --help 2>&1 | grep -m1 -o 'tailwindcss v[0-9.]*' || true)"
if [[ "${version_line}" != "tailwindcss ${PINNED_VERSION}" ]]; then
    echo "build_tailwind.sh: expected tailwindcss ${PINNED_VERSION}, got '${version_line:-unknown}'." >&2
    exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"${TAILWIND_BIN}" \
    --config "${repo_root}/scripts/tailwind.config.js" \
    --input "${repo_root}/scripts/tailwind.input.css" \
    --output "${repo_root}/ui/static/css/tailwind.generated.css" \
    --minify
