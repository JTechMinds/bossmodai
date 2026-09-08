"""Design tokens meet WCAG 2.2 AA and stay in sync with the Tailwind config."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "ui" / "static" / "css" / "tokens.css"
TW_CONFIG = ROOT / "ui" / "static" / "js" / "tailwind-config.js"

# Tokens used for text. Each must clear 4.5:1 on BOTH surfaces.
TEXT_TOKENS = ("ink", "muted", "hint", "accent", "alert", "ok")
SURFACES = ("#ffffff", "#f6f7f9")

# Tokens used only for dots, bar fills, and control borders (SC 1.4.11 -> 3:1).
NON_TEXT_TOKENS = {"ok-mark": 3.0, "line-control": 3.0}

# Inks that are never used on --bg or --panel: each one is text ON its tint, so
# it is measured against that tint and nothing else. --ok-ink joined them when
# the visual pass measured --ok on --ok-bg at 4.33 and found it under the floor,
# and --alert-ink joined for the same reason at 4.29 on --alert-bg.
#
# Pairs, not a mapping: one ink can carry more than one tint. --blue-ink is the
# text on --accent-bg as well as on --blue, because --accent measures 4.19 on
# its own tint and minting a second near-identical navy for it would be two
# tokens for one colour.
INK_ON_TINT = (
    ("ok-ink", "ok-bg"),
    ("alert-ink", "alert-bg"),
    ("blue-ink", "blue"),
    ("blue-ink", "accent-bg"),
    ("amber-ink", "amber"),
    ("teal-ink", "teal"),
    ("pink-ink", "pink"),
)


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens() -> dict[str, str]:
    text = TOKENS.read_text(encoding="utf-8")
    return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", text))


def test_text_tokens_meet_aa_on_both_surfaces() -> None:
    tokens = _tokens()
    failures = []
    for name in TEXT_TOKENS:
        assert name in tokens, f"--{name} missing from tokens.css"
        for surface in SURFACES:
            ratio = contrast(tokens[name], surface)
            if ratio < 4.5:
                failures.append(f"--{name} {tokens[name]} on {surface}: {ratio:.2f}")
    assert not failures, "WCAG AA failures: " + "; ".join(failures)


def test_non_text_tokens_meet_component_contrast() -> None:
    tokens = _tokens()
    for name, floor in NON_TEXT_TOKENS.items():
        assert name in tokens, f"--{name} missing from tokens.css"
        ratio = contrast(tokens[name], "#ffffff")
        assert ratio >= floor, f"--{name} {tokens[name]}: {ratio:.2f} < {floor}"


def test_ink_on_tint_pairs_meet_aa() -> None:
    """A tint's ink is measured on that tint, because that is where it is used.

    --ok was the counter-example: it clears 4.55:1 on --bg, which is what
    TEXT_TOKENS above proves, and 4.33:1 on --ok-bg, which nothing proved. The
    Office state pill and the Log's Active badge are both small bold text on
    --ok-bg, so both were failing while a green test said the token was fine.

    --alert and --accent were the same counter-example one round later: 4.57
    and 4.51 on --bg, 4.29 and 4.19 on their own tints, and every error panel
    and selected row in the app is text on the tint.
    """
    tokens = _tokens()
    failures = []
    for ink, tint in INK_ON_TINT:
        assert ink in tokens, f"--{ink} missing from tokens.css"
        assert tint in tokens, f"--{tint} missing from tokens.css"
        ratio = contrast(tokens[ink], tokens[tint])
        if ratio < 4.5:
            failures.append(f"--{ink} on --{tint}: {ratio:.2f}")
    assert not failures, "WCAG AA failures: " + "; ".join(failures)


def test_tailwind_config_mirrors_tokens() -> None:
    """tokens.css is the source of truth; the Tailwind config must agree."""
    tokens = _tokens()
    tw = TW_CONFIG.read_text(encoding="utf-8")
    pairs = {
        "bg": "bg", "surface": "panel", "border": "line",
        "text": "ink", "muted": "muted", "hint": "hint", "accent": "accent",
    }
    for tw_name, token_name in pairs.items():
        expected = tokens[token_name]
        pattern = rf"['\"]?{tw_name}['\"]?\s*:\s*['\"]{expected}['\"]"
        assert re.search(pattern, tw, re.IGNORECASE), (
            f"tailwind-config.js {tw_name} must be {expected} (from --{token_name})"
        )
