"""Design tokens meet WCAG 2.2 AA and stay in sync with the Tailwind config."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "ui" / "static" / "css"
TOKENS = CSS / "tokens.css"
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


# ─── Text that lands on a tint because of WHERE it renders ───

# The pairs above prove a tint's own ink. What they cannot see is a rule
# written for the neutral page that ends up inside a tinted container:
# .task-card-age is --hint, which is 4.51 on --bg and 4.19 on --accent-bg, and
# selecting the card swaps the ground under it without touching the rule.
#
# So every tinted surface is named with the container that paints it, and the
# rules that render inside it are resolved the way the cascade resolves them —
# a scoped override, then the element's own rule, then the colour inherited
# from the container, then --ink from `html, body` in base.css.
#
# `prefix` is this codebase's BEM-ish child naming: every `.task-card-*` rule
# renders inside a `.task-card`, so a NEW child rule is covered without anyone
# remembering to come back here. `extra` names the descendants that do not
# share the container's prefix — .roster-status sits inside .roster-row, not
# inside a .roster-row-*.
#
# A descendant that paints its OWN background is skipped: .task-card-marker is
# --pink-ink on --pink wherever the card sits, and INK_ON_TINT already proves
# that pair. That is an exclusion by property, not by name.


class TintedSurface(NamedTuple):
    """One container that paints a tint, and the rules that render on it.

    Attributes:
        sheet: Stylesheet under ui/static/css that holds every rule involved.
        container: The exact selector that paints the tint.
        tint: Token name the container sets as its background. Asserted, so a
            container that stops using this tint fails loudly instead of being
            measured against a colour it no longer paints.
        prefix: BEM-ish child prefix; every `.{prefix}-*` rule in `sheet` is
            treated as rendering inside `container`.
        extra: Descendant classes that do not carry `prefix`.
    """

    sheet: str
    container: str
    tint: str
    prefix: str
    extra: tuple[str, ...] = ()


TINTED_SURFACES = (
    # Selecting a roster row tints the whole row; the name, the status line and
    # the last-spoken time all render on it (shell/roster-people.js,
    # shell/roster-threads.js, shell/roster-row-meta.js).
    TintedSurface(
        "shell.css", '.roster-row[data-selected="true"]', "accent-bg", "roster-row",
        ("roster-name", "roster-status", "roster-time"),
    ),
    # Selecting a Board card tints the card behind everything task-card.js
    # builds into it.
    TintedSurface("places.css", ".task-card.is-selected", "accent-bg", "task-card"),
    # The assign sheet's informational outcome (places/board/assign-outcomes.js).
    TintedSurface("places.css", '.assign-panel[data-tone="info"]', "accent-bg", "assign-panel"),
    # The operator's own turn in a transcript (conversation/message.js).
    TintedSurface("conversation.css", ".msg-human", "accent-bg", "msg"),
    # Every event card is tinted: EVENT_TONES in conversation/event-cards.js
    # maps all four tones onto these three treatments, so `.event-card`'s own
    # --panel ground never reaches the screen and the error line inside one is
    # always text on a tint. All three, because the same rule lands on three
    # different grounds and the weakest of them is the one that decides.
    TintedSurface("conversation.css", ".event-card.tone-alert", "alert-bg", "event-card"),
    TintedSurface("conversation.css", ".event-card.tone-amber", "amber", "event-card"),
    TintedSurface("conversation.css", ".event-card.tone-ok", "ok-bg", "event-card"),
)

# The colour every element starts from: `html, body` in base.css.
ROOT_INK = "ink"


def _css_rules(css: str) -> list[tuple[str, str]]:
    """Every (selector, declaration block) pair in a stylesheet.

    Comments are stripped first. At-rules are stepped INTO rather than over, so
    a rule nested in an `@media` block is returned like any other — a
    responsive override that repaints text on a tint is exactly the kind of
    rule that would otherwise be invisible here. Selector lists are split, so
    `.a, .b { … }` returns the same block twice.
    """
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: list[tuple[str, str]] = []
    index = 0
    while True:
        brace = text.find("{", index)
        if brace == -1:
            return rules
        prelude = " ".join(re.sub(r"^[\s}]+", "", text[index:brace]).split())
        if prelude.startswith("@"):
            index = brace + 1  # Step inside; its children are real rules.
            continue
        depth, end = 1, brace + 1
        while end < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        block = text[brace + 1:end - 1]
        for selector in prelude.split(","):
            selector = " ".join(selector.split())
            if selector:
                rules.append((selector, block))
        index = end


def _declared_color(block: str) -> str | None:
    """The token a block sets as `color:`, or None.

    The lookbehind is what keeps `border-color` and `background-color` out: a
    card whose border is --accent says nothing about the text on it.
    """
    match = re.search(r"(?<![\w-])color\s*:\s*var\(--([a-z0-9-]+)\)", block)
    return match.group(1) if match else None


def _paints_background(block: str) -> bool:
    """Whether a block gives its element a ground of its own."""
    return re.search(r"(?<![\w-])background(?:-color|-image)?\s*:", block) is not None


def _base_class(selector: str) -> str:
    """`.task-card` from `.task-card.is-selected`, `.msg-human` from itself."""
    match = re.match(r"\.[a-z][a-z0-9-]*", selector)
    assert match, f"container {selector} does not start with a class"
    return match.group(0)


def _tinted_surface_failures(surface: TintedSurface, tokens: dict[str, str]) -> list[str]:
    """Resolve the colour of every rule rendering on `surface` and measure it."""
    rules = _css_rules((CSS / surface.sheet).read_text(encoding="utf-8"))
    base = _base_class(surface.container)

    own: dict[str, list[str]] = {}
    scoped: dict[str, list[str]] = {}
    container_blocks: list[str] = []
    base_blocks: list[str] = []
    for selector, block in rules:
        if selector == surface.container:
            container_blocks.append(block)
        if selector == base:
            base_blocks.append(block)
        single = re.fullmatch(r"\.([a-z][a-z0-9-]*)", selector)
        if single:
            own.setdefault(single.group(1), []).append(block)
        nested = re.fullmatch(
            rf"{re.escape(surface.container)}\s+\.([a-z][a-z0-9-]*)", selector)
        if nested:
            scoped.setdefault(nested.group(1), []).append(block)

    # The table describes the stylesheet, or it describes nothing.
    assert container_blocks, f"{surface.sheet}: no rule for {surface.container}"
    assert any(
        re.search(rf"(?<![\w-])background(?:-color)?\s*:\s*var\(--{surface.tint}\)", block)
        for block in container_blocks
    ), f"{surface.sheet}: {surface.container} no longer paints --{surface.tint}"
    for name in surface.extra:
        assert name in own, (
            f"{surface.sheet}: .{name} is listed inside {surface.container} but has no rule"
        )

    def last_color(blocks: list[str]) -> str | None:
        for block in reversed(blocks):
            token = _declared_color(block)
            if token:
                return token
        return None

    inherited = last_color(container_blocks) or last_color(base_blocks) or ROOT_INK
    candidates = {name for name in own if name.startswith(f"{surface.prefix}-")}
    candidates.update(surface.extra)
    assert candidates, f"{surface.sheet}: nothing renders inside {surface.container}"

    failures = []
    for name in sorted(candidates):
        blocks = own.get(name, [])
        if any(_paints_background(block) for block in blocks):
            continue  # Its own ground; INK_ON_TINT measures that pair.
        token = last_color(scoped.get(name, [])) or last_color(blocks) or inherited
        assert token in tokens, f"{surface.sheet}: .{name} uses unknown --{token}"
        ratio = contrast(tokens[token], tokens[surface.tint])
        if ratio < 4.5:
            failures.append(
                f"{surface.sheet} .{name} inside {surface.container}: "
                f"--{token} on --{surface.tint} is {ratio:.2f}"
            )
    return failures


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

    The second half closes the gap the first half cannot see. A pair proves a
    token that was CHOSEN for a tint; it says nothing about a rule that merely
    ends up on one. .task-card-age, .task-card-subs and .task-card-parent were
    all --hint at 4.19 inside a selected card, while every pair below was
    green — the same defect .roster-row[data-selected] had already been fixed
    for, one stylesheet away.
    """
    tokens = _tokens()
    failures = []
    for ink, tint in INK_ON_TINT:
        assert ink in tokens, f"--{ink} missing from tokens.css"
        assert tint in tokens, f"--{tint} missing from tokens.css"
        ratio = contrast(tokens[ink], tokens[tint])
        if ratio < 4.5:
            failures.append(f"--{ink} on --{tint}: {ratio:.2f}")
    for surface in TINTED_SURFACES:
        failures.extend(_tinted_surface_failures(surface, tokens))
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
