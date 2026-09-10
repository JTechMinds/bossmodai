"""The one seam where HTML text becomes nodes, and the allowlist that guards it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
MARKDOWN = JS / "core" / "markdown.js"
STYLESHEET = CSS / "markdown.css"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_markdown_harness.cjs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _app_js() -> list[Path]:
    return [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.parts]


def _rules(css: str) -> str:
    """A stylesheet with its comments removed.

    Read against the RULES, not the prose: conversation.css explains at length
    why `pre-wrap` is gone, and a scan of the raw text would find the word in
    that explanation and call the rule present.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_markdown_harness() -> None:
    """Every way an agent turn could have reached into the page.

    The parse step is stubbed because Node has no DOMParser; the harness
    supplies the tree a parser would have produced. `marked` is a vendored
    library with its own suite — what this module adds on top of it operates on
    nodes, and that is what runs here.
    """
    result = subprocess.run(
        ["node", str(HARNESS), str(MARKDOWN)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        # Removed with their contents: unwrapping either one is its own bug.
        "scriptRemovedWithContents": True,
        "styleRemovedWithContents": True,
        "iframeRemoved": True,
        "handlerAttributeStripped": True,
        "nestedHandlerStripped": True,
        # marked v15 dropped `sanitize` and filters no schemes, so this is ours.
        "javascriptHrefUnwrapped": True,
        "dataHrefUnwrapped": True,
        "httpsHrefKeptAndHardened": True,
        "mailtoHrefKept": True,
        "relativeHrefResolved": True,
        "targetInjectionOverwritten": True,
        "imageBecomesAltText": True,
        # Unwrapped, never deleted: text is not lost to a tag we do not know.
        "unknownTagUnwrappedKeepingText": True,
        "unwrappedChildrenAreStillSanitised": True,
        "commentRemoved": True,
        "codeLanguageClassKept": True,
        "unknownLanguageClassDropped": True,
        "mixedCaseLanguageNormalised": True,
        "bogusCodeClassDropped": True,
        "classOnAnyOtherTagDropped": True,
        "taskCheckboxKeptAndDisabled": True,
        "nonCheckboxInputRemoved": True,
        "tableWrappedForScroll": True,
        "tableAlignmentKept": True,
        "orderedListStartKept": True,
        "renderPassesGfmAndBreaks": True,
        "renderReturnsNodesNotMarkup": True,
        "renderSanitisesWhatTheParserProduced": True,
        "renderHighlightsFencedCodeOnly": True,
        # An untagged fence is never guessed at: highlightAuto reads Go as C#
        # and a diff as CSS, and colour that is confidently wrong reads as
        # meaning. GitHub renders it plain and so do we.
        "untaggedFenceKeepsSurfaceButNoColour": True,
        "unknownLanguageFenceFallsBackToUntagged": True,
        "renderOfEmptyTextIsEmpty": True,
    }


def test_the_parser_has_exactly_one_owner() -> None:
    """DOMParser lives in core/markdown.js and nowhere else in the tree.

    It used to live in places/files/file-content.js, whose docstring named its
    trust boundary as "the operator's own workspace markdown". Chat bodies are
    agent output, so reusing that renderer as it stood would have moved
    untrusted text through a path built for trusted text. Moving it here is
    what makes one allowlist cover both.
    """
    parsers = sorted(
        path.relative_to(JS).as_posix() for path in _app_js()
        if "DOMParser" in _read(path)
    )
    assert parsers == ["core/markdown.js"], f"HTML is parsed in more than one place: {parsers}"

    # And one owner for the library, so a second call site cannot pick its own
    # options and render the same message two ways.
    callers = sorted(
        path.relative_to(JS).as_posix() for path in _app_js()
        if re.search(r"\bmarked\.parse\(", _read(path))
    )
    assert callers == ["core/markdown.js"], callers


def test_sanitising_precedes_highlighting() -> None:
    """The order IS the reason <span> is not on the allowlist.

    hljs emits `<span class="hljs-*">` by the dozen. Highlighting first would
    force the allowlist to admit a classed span, which is the one element an
    agent would need to borrow the shell's own styling. Highlighting second
    means those spans are added to a tree nothing inspects again.
    """
    source = _read(MARKDOWN)
    assert source.index("sanitize(") < source.index("highlightCode(")
    body = source.split("function render(", 1)[1]
    assert body.index("sanitize(") < body.index("highlightCode(")
    assert "'SPAN'" not in source and '"SPAN"' not in source


def test_the_allowlist_is_a_whitelist_not_a_blocklist() -> None:
    """A denied-tag list is a list somebody has to keep up to date.

    Attributes are the same shape of decision: nothing is removed by name, so
    an `onpointerrawupdate` nobody has heard of is dropped by the same rule
    that drops `onclick`.
    """
    source = _read(MARKDOWN)
    assert "const ALLOWED = Object.freeze({" in source
    assert "const ALLOWED_SCHEMES = Object.freeze(" in source
    # The one thing that must never be reachable through an allowlist entry.
    for tag in ("'SCRIPT'", "'STYLE'", "'IFRAME'", "'OBJECT'", "'EMBED'"):
        assert tag in source.split("const STRIPPED", 1)[1].split("}", 1)[0], (
            f"{tag} is not in the removed-with-contents set"
        )
    # No `startsWith('on')` anywhere: that reads as a blocklist and would
    # suggest the allowlist has a hole it is patching.
    assert "startsWith('on')" not in source


def test_markdown_is_loaded_and_styled() -> None:
    """The module and its prose vocabulary are both wired into the page."""
    html = _read(HTML)
    assert "static_url('js/core/markdown.js')" in html
    assert "static_url('css/markdown.css')" in html
    # After the vendored libraries it reads, and before every surface that
    # renders a message.
    scripts = re.findall(r"static_url\('([^']+\.js)'\)", html)
    assert scripts.index("js/vendor/marked.min.js") < scripts.index("js/core/markdown.js")
    assert scripts.index("js/vendor/highlight.min.js") < scripts.index("js/core/markdown.js")
    assert scripts.index("js/core/markdown.js") < scripts.index("js/conversation/message.js")
    assert scripts.index("js/core/markdown.js") < scripts.index("js/places/files/file-content.js")

    sheets = re.findall(r"static_url\('(css/[^']+\.css)'\)", html)
    assert sheets.index("css/controls.css") < sheets.index("css/markdown.css")
    assert sheets.index("css/markdown.css") < sheets.index("css/conversation.css")


def test_the_prose_stylesheet_uses_tokens_only() -> None:
    """Same rule every other sheet follows: colour comes from tokens.css.

    Read against the RULES throughout. An earlier version of this test scanned
    the raw file for `.md code` and passed on the sentence explaining why that
    selector is deliberately NOT used — an assertion satisfied by prose proves
    nothing about the stylesheet.
    """
    source = _rules(_read(STYLESHEET))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), "markdown.css hardcodes a colour"
    # It is the shared vocabulary, so it is scoped to the class and not to a
    # surface: one `.md` block, used by the transcript and the file viewer.
    for surface in (".msg", ".file-view", ".transcript"):
        assert surface not in source, f"markdown.css reaches into {surface}"
    for element in (".md p", ".md h1", ".md ul", ".md ol", ".md li",
                    ".md blockquote", ".md table", ".md th", ".md a", ".md hr",
                    ".md pre", ".md pre code", ".md-scroll"):
        assert element in source, f"{element} has no rule"
    # Inline code is styled through `:not(pre) > code` and never through a bare
    # `.md code`: at (0,1,1) that would outrank the vendored `.hljs` theme and
    # repaint every fenced block with the inline background.
    assert ".md :not(pre) > code" in source
    assert not re.search(r"\.md code\s*[,{]", source), "a bare `.md code` rule would beat .hljs"


def test_both_surfaces_render_through_the_one_module() -> None:
    """The transcript and the file viewer share the renderer and the styling.

    Two markdown renderers with two trust boundaries is the state this
    replaces; asserting both call sites is what stops it coming back one
    convenience at a time.
    """
    message = _read(JS / "conversation" / "message.js")
    assert "BossModMarkdown.render(" in message
    assert "'msg-body md'" in message

    content = _read(JS / "places" / "files" / "file-content.js")
    assert "BossModMarkdown.renderInto(" in content
    assert "DOMParser" not in content, "file-content.js parses HTML again"

    viewer = _read(JS / "places" / "files" / "file-viewer.js")
    assert "'file-view-rendered md'" in viewer


def test_the_body_no_longer_preserves_whitespace() -> None:
    """`pre-wrap` plus `breaks: true` is every line break rendered twice.

    The bubble's whitespace is markdown's job now. `pre` keeps its own, which
    is the one place it still has to survive.
    """
    conversation = _rules(_read(CSS / "conversation.css"))
    assert "pre-wrap" not in conversation, "the conversation surface still preserves whitespace"
    assert "white-space: pre" in _read(STYLESHEET).split(".md pre", 1)[1].split("}", 1)[0]
