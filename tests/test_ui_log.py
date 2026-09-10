"""The Log: two feeds, one surface.

Spec 6.6. activity.js and diagnostics.js both answered "what happened" and were
two views with two filter bars, two row renderers and two WebSocket handlers.
The tests that matter most here are the ones that stop them becoming two again.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
LOG = JS / "places" / "log"
HTML = ROOT / "ui" / "templates" / "index.html"

# The wire keys the two feeds speak. Exactly one file may read them.
WIRE_KEYS = (
    "agent_name", "agent_id", "created_at", "timestamp", "is_active",
    "trigger_type", "trigger_data", "action_name", "total_tokens", "duration_ms",
)


def _read(name: str) -> str:
    return (LOG / name).read_text(encoding="utf-8")


def _modules() -> list[Path]:
    return sorted(LOG.glob("*.js"))


def test_log_merges_both_feeds_into_one_row_type() -> None:
    """Two feeds normalise at one boundary; nothing above it knows there were two.

    log-source.js owns both REST loads and all three subscriptions. log-shape.js
    is the only file that reads a backend key — the same arrangement
    needs/need-shape.js has, and for the same reason. If a second file started
    reading `created_at`, the merge would have leaked.
    """
    source = _read("log-source.js")
    assert "bus.subscribe('activity'" in source
    assert "bus.subscribe('activity_update'" in source
    assert "bus.subscribe('diagnostic'" in source
    assert "`/api/activity/feed?${params}`" in source
    assert "`/api/diagnostics?${params}`" in source
    assert "SHAPE.fromActivity" in source
    assert "SHAPE.fromDiagnostic" in source
    # One merged list, deduped by the key both feeds produce.
    assert "function rows()" in source
    assert "seen.has(row.key)" in source

    shape = _read("log-shape.js")
    for key in WIRE_KEYS:
        assert key in shape, f"log-shape.js must be the file that converts {key}"
    # Nothing else in the row pipeline may read a row's wire shape.
    # diagnostic-detail.js is exempt for one reason and one only: it consumes a
    # THIRD payload, GET /api/diagnostics/{id}, which is fetched on demand, is
    # never a row, and is read in exactly this one file. It is asserted below
    # to touch none of the row keys, so the exemption cannot become a leak.
    for path in _modules():
        if path.name in {"log-shape.js", "diagnostic-detail.js"}:
            continue
        text = path.read_text(encoding="utf-8")
        for key in WIRE_KEYS:
            # Dotted access is reading the shape. log-source.js names
            # `agent_name` and `agent_id` as QUERY parameters, which is the
            # server's request contract, not a row field it has read.
            assert f".{key}" not in text, f"{path.name} reads the wire key {key}"
    detail = _read("diagnostic-detail.js")
    for key in ("timestamp", "is_active", "agent_name", "created_at", "trigger_type"):
        assert f".{key}" not in detail, f"diagnostic-detail.js reads the row wire key {key}"

    # The renderer reads LogRow and nothing else. No branch on the feed.
    row = _read("log-row.js")
    assert "diagnosticId" not in row, "log-row.js branches on which feed a row came from"
    for marker in ("'activity:'", "'diagnostic:'", "startsWith("):
        assert marker not in row, f"log-row.js inspects the key prefix ({marker})"
    assert "row.expandable" in row
    # One row renderer, and the place uses only it.
    place = _read("log-place.js")
    assert place.count("ROW.renderRow(") == 1
    assert "renderDiagnosticRow" not in place and "renderActivityRow" not in place


def test_diagnostic_expands_in_place_not_in_a_second_view() -> None:
    """The dock-era detail hid the office canvas and took the centre pane.

    Reading one turn meant leaving the feed, and closing it fired a
    `panel-resize` to bring the canvas back. Now the expansion is a child of the
    row that opened it, and both feeds' rows expand through the same renderer:
    a diagnostic's expansion simply has a trace to fetch as well.
    """
    row = _read("log-row.js")
    assert "if (expanded && detail) element.append(detail);" in row
    assert "'aria-expanded': expanded ? 'true' : 'false'" in row

    detail = _read("diagnostic-detail.js")
    assert "function createDetail(deps)" in detail
    # No second view, and no reaching for the dock-era containers it replaced.
    for dock in ("canvas-container", "diagnostic-detail-panel", "diag-detail-body",
                 "getElementById", "panel-resize", "BossModApp"):
        assert dock not in detail, f"diagnostic-detail.js still reaches for {dock}"

    # Everything the old panel could do: trace, sections, copy, JSON formatting.
    assert "function formatJson(" in detail
    assert "'Execution Trace'" in detail
    assert "'Trigger'" in detail
    assert "'Context Sent'" in detail
    assert "'Raw Response'" in detail
    assert "'Parsed Action'" in detail
    assert "'Execution Result'" in detail
    assert "section('Error'" in detail
    assert "'aria-expanded': 'true'" in detail
    assert "navigator.clipboard.writeText(pre.textContent)" in detail
    # The unescaping the trace is unreadable without.
    assert ".replace(/\\\\n/g, '\\n')" in detail
    # A copy that failed says so; diagnostics.js only logged it.
    assert "status.textContent = 'Could not copy';" in detail
    # A failed detail fetch offers a retry rather than a blank panel.
    assert "'Try again'" in detail

    # Reopening a row already read must not refetch it.
    place = _read("log-place.js")
    assert "details.has(row.key)" in place
    assert "// The node is kept: reopening must not refetch a turn already read." in place


def test_follow_never_yanks_a_reading_operator() -> None:
    """One scroll rule, shared with the transcript rather than rewritten.

    The transcript appends at the bottom and the Log prepends at the top, so
    they watch opposite edges — but "never move the viewport of an operator who
    has scrolled away" is one rule with one threshold, and it lives in
    core/dom.js so the two cannot drift apart.
    """
    dom = (JS / "core" / "dom.js").read_text(encoding="utf-8")
    assert "function isNearEdge(el, edge, threshold)" in dom
    assert "const STICK_THRESHOLD_PX = 80;" in dom
    assert "if (edge === 'top') return el.scrollTop <= slack;" in dom
    assert "el.scrollHeight - el.scrollTop - el.clientHeight <= slack" in dom
    # An unknown edge throws rather than silently answering "yes" and yanking.
    assert "throw new Error(`[dom] isNearEdge: unknown edge" in dom

    transcript = (JS / "conversation" / "transcript.js").read_text(encoding="utf-8")
    assert "return isNearEdge(scroller, 'bottom');" in transcript
    assert "STICK_THRESHOLD_PX = 80" not in transcript, "a second copy of the threshold"

    place = _read("log-place.js")
    assert "const stick = filters.following() && isNearEdge(scrollEl, 'top');" in place
    assert "const previousScroll = scrollEl.scrollTop;" in place
    assert "scrollEl.scrollTop = stick ? 0 : previousScroll;" in place
    # Sticking needs BOTH: Follow on, and the operator already at the edge.
    stick_line = [line for line in place.splitlines() if "const stick =" in line][0]
    assert "&&" in stick_line, "Follow alone must not be enough to move the view"

    filters = _read("log-filters.js")
    # Follow is the shared core/switch.js control now. The state it announces
    # is the switch's job (aria-checked on a role=switch button, asserted on
    # the built node in test_ui_visual_parity.py); what stays this module's job
    # is starting from the current value and reporting every change onward.
    assert "BossModSwitch.create(" in filters
    assert "pressed: follow" in filters
    assert "onFollow(follow)" in filters

    # The same principle one level down: rebuilding a <select> closes it, so a
    # row landing while the operator has the agent list open must not take it
    # away from them. The options are replaced only when the set changed.
    paint_body = place.split("function paint() {", 1)[1].split("\n    }", 1)[0]
    assert "if (signature !== agentSignature) {" in paint_body
    assert paint_body.count("filters.setAgents(") == 1, (
        "the agent select must be rebuilt from exactly one guarded place"
    )
    assert paint_body.index("if (signature !== agentSignature) {") < paint_body.index(
        "filters.setAgents("
    )


def test_log_reads_agent_and_diagnostic_deep_links() -> None:
    """A Metrics token bar and an error need both land here, pre-filtered.

    The filter bar takes the agent id from placeParams; the place opens the
    named diagnostic once the first page is in. A link to a turn older than that
    window says so rather than opening nothing.
    """
    place = _read("log-place.js")
    assert "const params = ctx.store.getState().placeParams;" in place
    assert "agentId: params.agentId || ''" in place
    assert "if (params.diagnosticId) expandDeepLink(String(params.diagnosticId));" in place
    assert "const key = `diagnostic:${diagnosticId}`;" in place
    assert "setError('That diagnostic is not in the most recent turns.');" in place

    # The deep-linked id is resolved to a NAME before the first read, because
    # the activity feed narrows by name and carries no agent id at all.
    assert "filters.setAgents(agentOptions([]));" in place
    first_read = place.split("mount(el, ctx) {", 1)[1]
    assert first_read.index("filters.setAgents(agentOptions([]));") < first_read.index(
        "void reload()"
    )

    # Changing a filter RE-READS both feeds; it does not only re-filter what
    # is already loaded. The activity feed pages, so an agent whose rows are
    # older than the first page would otherwise vanish when you filtered to
    # them — which is exactly why activity.js re-fetched on every change.
    assert "onChange: () => { void reload(true); }" in place
    assert "await source.load(filters.filters());" in place
    # Quietly: a skeleton flashing on every keystroke is its own defect.
    assert "async function reload(quiet)" in place
    assert "loading = !quiet;" in place

    filters = _read("log-filters.js")
    assert "let chosen = { id: agentId || '', name: '' };" in filters
    # Both feeds identify agents differently, so the filter carries both and
    # matches on either — an id-only filter would hide every activity row,
    # because the unified feed has no agent id at all.
    assert "agentName: chosen.name" in filters
    source = _read("log-source.js")
    assert "const byId = agentId && row.agentId === agentId;" in source
    assert "const byName = agentName && row.agentName === agentName;" in source

    # The other end of the link: Metrics navigates here with an agent id.
    metrics = (JS / "places" / "metrics" / "metrics-place.js").read_text(encoding="utf-8")
    assert "ctxRef.navigate('log', { agentId })" in metrics


def test_log_modules_stay_focused() -> None:
    """Modular, no embedded markup, no hardcoded colour, and it drains."""
    banned_colours = re.compile(
        r"\b(?:bg|text|border)-(?:emerald|red|amber|blue|slate|purple|rose|cyan|indigo|teal|pink)-\d{2,3}\b"
    )
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        lines = len(source.splitlines())
        assert lines < 400, f"{path.name} is {lines} lines"
        assert "innerHTML" not in source, f"{path.name} builds markup from a string"
        assert "insertAdjacentHTML" not in source, f"{path.name} injects markup"
        assert "'undefined'" not in source, f"{path.name} probes for a global"
        assert not banned_colours.search(source), f"{path.name} hardcodes a colour class"
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), f"{path.name} hardcodes a hex"

    place = _read("log-place.js")
    assert "BossModPlaces.register('log', BossModLogPlace)" in place
    assert place.count("h('h1'") == 1
    assert "h('h1', { tabindex: '-1' }, 'Log')" in place
    unmount = place.split("unmount() {", 1)[1]
    assert "disposers.splice(0).forEach((off) => off());" in unmount
    assert "filters.destroy();" in unmount
    assert "source.destroy();" in unmount

    # The source owns its own subscriptions and lets them go.
    source = _read("log-source.js")
    destroy = source.split("destroy() {", 1)[1]
    assert "disposers.splice(0).forEach((off) => off());" in destroy
    assert "listeners.clear();" in destroy
    assert "load.next();" in destroy

    # Loaded in dependency order.
    scripts = re.findall(r"static_url\('([^']+\.js)'\)", HTML.read_text(encoding="utf-8"))
    for earlier, later in (
        ("js/places/log/log-shape.js", "js/places/log/log-source.js"),
        ("js/places/log/log-row.js", "js/places/log/log-place.js"),
        ("js/places/log/log-filters.js", "js/places/log/log-place.js"),
        ("js/places/log/diagnostic-detail.js", "js/places/log/log-place.js"),
    ):
        assert scripts.index(earlier) < scripts.index(later), f"{earlier} must load before {later}"
