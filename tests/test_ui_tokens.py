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
