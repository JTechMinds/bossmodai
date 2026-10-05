"""The pre-built Tailwind sheet covers every utility the source names.

The Tailwind Play runtime compiled CSS in the page for whatever classes were in
the DOM, so a class built at runtime (`bg-${tone}`) still got its rule. The
pre-built ui/static/css/tailwind.generated.css (scripts/build_tailwind.sh, the
standalone CLI v3.4.17) only knows class names written out literally in the
source. This file is the drift guard that replaces the runtime:

- every Tailwind-shaped token in a string literal under ui/static/js (vendor
  excluded) or in ui/templates/index.html must be defined in the generated
  sheet or be an app class from ui/static/css/*.css — a new utility without a
  rebuild fails here instead of rendering unstyled;
- no Tailwind utility may be assembled at runtime from a fragment, because the
  CLI cannot see it: write the full class per variant (a lookup map of
  complete strings);
- the compiler itself stays gone.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
GENERATED = CSS / "tailwind.generated.css"
INDEX = ROOT / "ui" / "templates" / "index.html"

# Utility roots: a token is Tailwind-shaped when it is `<root>-<value>`,
# optionally behind variants (`hover:`, `md:`, `group-hover:`) and a `-` or `!`.
_ROOTS = (
    "bg", "text", "border", "ring", "ring-offset", "outline", "divide", "fill", "stroke",
    "w", "h", "min-w", "min-h", "max-w", "max-h", "size",
    "p", "px", "py", "pt", "pb", "pl", "pr", "ps", "pe",
    "m", "mx", "my", "mt", "mb", "ml", "mr", "ms", "me",
    "gap", "gap-x", "gap-y", "space-x", "space-y",
    "grid-cols", "grid-rows", "col-span", "row-span", "col-start", "row-start",
    "rounded", "font", "leading", "tracking", "opacity", "shadow", "z",
    "top", "left", "right", "bottom", "inset", "inset-x", "inset-y",
    "translate-x", "translate-y", "scale", "rotate", "origin",
    "flex", "items", "justify", "self", "content", "place-items", "place-content", "order",
    "basis", "grow", "shrink", "line-clamp", "overflow", "overflow-x", "overflow-y",
    "whitespace", "break", "cursor", "select", "pointer-events", "decoration", "underline-offset",
    "placeholder", "accent", "caret", "duration", "ease", "delay", "transition", "animate",
    "object", "aspect", "columns", "align", "backdrop", "blur", "from", "via", "to",
)
# Roots whose values are only numbers or arbitrary `[...]`. Lucide icon names
# share their spelling (`rotate-ccw`, `list-todo`), so an alphabetic value
# after one of these is an icon, not a utility.
_NUMERIC_ROOTS = ("rotate", "scale", "opacity", "z", "duration", "delay", "list")
_NUMERIC_VALUE = re.compile(r"[0-9\[]")
_STANDALONE = (
    "flex", "inline-flex", "grid", "inline-grid", "block", "inline-block", "inline", "hidden",
    "truncate", "uppercase", "lowercase", "capitalize", "italic", "underline", "line-through",
    "rounded", "border", "shadow", "ring", "transition", "transition-colors", "shrink-0",
    "grow", "flex-1", "flex-col", "flex-row", "flex-wrap", "relative", "absolute", "fixed",
    "sticky", "sr-only", "antialiased", "tabular-nums", "font-mono", "font-sans",
    "overflow-hidden", "overflow-auto", "overflow-y-auto", "overflow-x-auto",
    "whitespace-nowrap", "whitespace-pre-wrap", "break-all", "break-words",
)
_VARIANTS = r"(?:[a-z0-9-]+:)*"
_SHAPED = re.compile(
    rf"^{_VARIANTS}!?-?(?:(?:{'|'.join(re.escape(r) for r in sorted(_ROOTS, key=len, reverse=True))})"
    rf"-[A-Za-z0-9./%#\[\]_-]+|(?:{'|'.join(re.escape(s) for s in _STANDALONE)}))$"
)
# A utility root followed by an interpolation: `bg-${tone}`, `text-' + x`.
_DYNAMIC = re.compile(
    rf"(?:^|[\s\"'`]){_VARIANTS}-?(?:{'|'.join(re.escape(r) for r in sorted(_ROOTS, key=len, reverse=True))})-$"
)
_SENTINEL = "\x00"
# Class tokens inside markup strings sit against `class="` and `">`.
_TOKEN_SPLIT = re.compile(r"[\s\"'`<>=]+")


def _js_files() -> list[Path]:
    return sorted(p for p in JS.rglob("*.js") if "vendor" not in p.relative_to(JS).parts)


def _literals(source: str) -> list[str]:
    """Every string literal's text in JS source, comments skipped.

    Template-literal interpolations become a sentinel in the outer text and
    their expressions are scanned for nested literals in turn, so both
    branches of `${active ? 'bg-a' : 'bg-b'}` are seen. `+` concatenation of
    a literal ending in a utility root is marked with the sentinel too.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j == -1 else j
        elif source.startswith("/*", i):
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c in "'\"":
            j = i + 1
            while j < n and source[j] != c:
                j += 2 if source[j] == "\\" else 1
            text = source[i + 1:j]
            rest = source[j + 1:j + 40].lstrip()
            out.append(text + (_SENTINEL if rest.startswith("+") else ""))
            i = j + 1
        elif c == "`":
            i = _template(source, i + 1, out)
        else:
            i += 1
    return out


def _template(source: str, i: int, out: list[str]) -> int:
    """Scan a template literal from just after its backtick; return the index past it."""
    text: list[str] = []
    n = len(source)
    while i < n and source[i] != "`":
        if source[i] == "\\":
            text.append(source[i:i + 2])
            i += 2
        elif source.startswith("${", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if source[j] in "'\"`":
                    # Skip a nested literal so its braces do not count.
                    sub = _literals(source[j:_literal_end(source, j)])
                    out.extend(sub)
                    j = _literal_end(source, j)
                    continue
                depth += {"{": 1, "}": -1}.get(source[j], 0)
                j += 1
            text.append(_SENTINEL)
            i = j
        else:
            text.append(source[i])
            i += 1
    out.append("".join(text))
    return i + 1


def _literal_end(source: str, i: int) -> int:
    quote, n = source[i], len(source)
    if quote == "`":
        return _template(source, i + 1, [])
    j = i + 1
    while j < n and source[j] != quote:
        j += 2 if source[j] == "\\" else 1
    return j + 1


def _shaped(token: str) -> bool:
    if not _SHAPED.match(token):
        return False
    bare = re.sub(rf"^{_VARIANTS}!?-?", "", token)
    for root in _NUMERIC_ROOTS:
        if bare.startswith(root + "-"):
            return bool(_NUMERIC_VALUE.match(bare[len(root) + 1:]))
    return True


def _unescape(selector: str) -> str:
    """CSS identifier escapes: hex (`\\32 xl` for a leading digit) and literal."""
    selector = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(int(m.group(1), 16)), selector)
    return re.sub(r"\\(.)", r"\1", selector)


def _class_selectors(css: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    names: set[str] = set()
    for block in re.finditer(r"([^{}]+)\{", css):
        for match in re.finditer(r"\.((?:\\[0-9a-fA-F]{1,6}\s?|\\.|[A-Za-z0-9_-])+)", block.group(1)):
            names.add(_unescape(match.group(1)))
    return names


def _sources() -> list[tuple[str, list[str]]]:
    sources = [(str(p.relative_to(ROOT)), _literals(p.read_text(encoding="utf-8"))) for p in _js_files()]
    html = INDEX.read_text(encoding="utf-8")
    sources.append((str(INDEX.relative_to(ROOT)), re.findall(r'class="([^"]*)"', html)))
    return sources


def test_generated_sheet_exists_and_is_built() -> None:
    assert GENERATED.is_file(), "run scripts/build_tailwind.sh"
    css = GENERATED.read_text(encoding="utf-8")
    # Preflight is in (Play's default): the reset rule and the bm theme colours.
    assert "box-sizing:border-box" in css
    assert ".bg-bm-bg{" in css


def test_every_tailwind_class_in_source_is_defined() -> None:
    defined = _class_selectors(GENERATED.read_text(encoding="utf-8"))
    app = set()
    for sheet in CSS.glob("*.css"):
        if sheet != GENERATED:
            app |= _class_selectors(sheet.read_text(encoding="utf-8"))
    missing: dict[str, set[str]] = {}
    for name, literals in _sources():
        for text in literals:
            for token in _TOKEN_SPLIT.split(text):
                if _SENTINEL in token or not _shaped(token):
                    continue
                if token not in defined and token not in app:
                    missing.setdefault(name, set()).add(token)
    assert not missing, (
        "Tailwind classes with no rule — rerun scripts/build_tailwind.sh: "
        + "; ".join(f"{name}: {sorted(tokens)}" for name, tokens in sorted(missing.items()))
    )


def test_no_tailwind_utility_is_built_at_runtime() -> None:
    offenders = []
    for name, literals in _sources():
        for text in literals:
            for piece in text.split(_SENTINEL)[:-1]:
                if _DYNAMIC.search(piece):
                    offenders.append(f"{name}: {piece.strip()[-40:]!r}")
    assert not offenders, (
        "A utility assembled at runtime is invisible to the CLI; write the full "
        "class per variant instead: " + "; ".join(offenders)
    )


def test_the_tailwind_runtime_is_not_loaded() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "tailwindcss.js" not in html
    assert "tailwind-config.js" not in html
    assert not (JS / "vendor" / "tailwindcss.js").exists()
    assert not (JS / "tailwind-config.js").exists()
