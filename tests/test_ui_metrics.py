"""The Metrics place: four states, shared formatters, and a way out of a number.

Spec 6.5. The dock-era pane had two states — a spinner and the dashboard — and
a failed fetch left the spinner spinning with the reason in the console. It also
kept private copies of two formatters that already existed, so a duration could
read one way here and another way everywhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
METRICS = JS / "places" / "metrics"
HTML = ROOT / "ui" / "templates" / "index.html"


def _read(name: str) -> str:
    return (METRICS / name).read_text(encoding="utf-8")


def _modules() -> list[Path]:
    return sorted(METRICS.glob("*.js"))


def test_metrics_registers_itself_and_has_four_states() -> None:
    """Registers, renders one focusable heading, and never leaves a spinner up.

    The endpoint is /api/metrics/dashboard. /api/metrics does not exist, and a
    404 here would have looked exactly like an empty company.
    """
    place = _read("metrics-place.js")
    assert "BossModPlaces.register('metrics', BossModMetricsPlace)" in place
    assert place.count("h('h1'") == 1
    assert "h('h1', { tabindex: '-1' }, 'Metrics')" in place
    assert "const ENDPOINT = '/api/metrics/dashboard';" in place
    assert "'/api/metrics'," not in place

    # Four states (spec 8.3).
    assert "function paintSkeleton()" in place
    assert "is-skeleton" in place
    assert "function paintEmpty()" in place
    assert "'Nothing measured yet'" in place
    assert "function paintError(" in place
    assert "'Try again'" in place
    assert "function paintDashboard(" in place
    # An empty company is not an error, and a grid of zeroes is not an empty
    # company: the place decides which it is looking at.
    assert "function isEmpty(" in place
    error_body = place.split("} catch (err) {", 1)[1].split("        }", 1)[0]
    assert "console.error('[metrics] could not load the dashboard', err);" in error_body
    assert "paintError(" in error_body

    # Every section §6.5 names has a producer.
    assert "CARDS.renderStatCards(data)" in place
    assert "BARS.renderAgentActivity(data)" in place
    assert "BARS.renderTaskDistribution(" in place
    assert "BARS.renderTokenUsage(" in place
    assert "CARDS.renderHealthGrid(data)" in place
    assert "CARDS.renderCommunication(" in place
    assert "CARDS.healthLine(data)" in place
    bars = _read("metric-bars.js")
    assert "FORMER_LABEL = 'Former agents'" in bars
    # The disclosure needs no endpoint: token totals outlive the agent that
    # produced them, so the roster is what says who has left. An EMPTY roster
    # means the first world_update has not landed yet, not that everyone left,
    # and filing the whole team under "Former agents" while the socket connects
    # would be a lie the operator has no way to question.
    assert "const knowWhoIsHere = live.size > 0;" in bars
    assert "const isFormer = knowWhoIsHere && !live.has(agent.agent_id);" in bars

    # Drains: the subscription and the in-flight read.
    unmount = place.split("unmount() {", 1)[1]
    assert "disposers.splice(0).forEach((off) => off());" in unmount
    assert "load.next();" in unmount
    assert "disposers.push(ctx.bus.subscribe('resync', () => BossModMetricsPlace.resync()))" in place

    # The place is loaded, and after its two renderers.
    html = HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"static_url\('([^']+\.js)'\)", html)
    assert scripts.index("js/places/metrics/metric-cards.js") < scripts.index(
        "js/places/metrics/metrics-place.js"
    )
    assert scripts.index("js/places/metrics/metric-bars.js") < scripts.index(
        "js/places/metrics/metrics-place.js"
    )


def test_metrics_reuses_shared_formatters() -> None:
    """company-metrics.js defined formatDuration and formatTokenCount locally.

    Both now live in BossModFormat beside formatNumber — utils.js in Phase 3B,
    core/format.js since Phase 4 split it — moved across verbatim so no
    displayed number changed. A private copy here would be a second opinion
    about what "< 1m" or "0" means.
    """
    fmt = (JS / "core" / "format.js").read_text(encoding="utf-8")
    assert "function formatDuration(seconds)" in fmt
    assert "function formatTokenCount(n)" in fmt
    assert "formatDuration," in fmt and "formatTokenCount," in fmt
    # Verbatim: the thresholds and the exact strings the pane always showed.
    assert "if (s < 60) return '< 1m';" in fmt
    assert "if (hours > 0) return `${hours}h ${minutes}m`;" in fmt
    assert "return formatNumber(n) + ' tokens';" in fmt

    cards = _read("metric-cards.js")
    bars = _read("metric-bars.js")
    assert "U.formatTokenCount(" in cards
    assert "U.formatTokenCount(" in bars
    assert "U.formatDuration(" in cards
    assert "const U = BossModFormat;" in cards
    assert "const U = BossModFormat;" in bars
    for source, name in ((cards, "metric-cards.js"), (bars, "metric-bars.js")):
        assert "function formatDuration(" not in source, f"{name} redefines formatDuration"
        assert "function formatTokenCount(" not in source, f"{name} redefines formatTokenCount"
        assert "function formatNumber(" not in source, f"{name} redefines formatNumber"

    # Each token bar is a way into the Log, filtered to that agent (spec 6.5).
    assert "onOpenLog(agent.agent_id)" in bars
    assert "'aria-label': `Show ${name} in the Log`" in bars
    assert "ctxRef.navigate('log', { agentId })" in _read("metrics-place.js")


def test_metrics_modules_stay_focused() -> None:
    """Modular, no embedded markup, no colour outside the tokens.

    company-metrics.js named Tailwind's greens, ambers and reds directly in the
    health cells, the status bars and the agent palette. Those are the five
    colours tokens.css exists to own.
    """
    banned_colours = re.compile(
        r"\b(?:bg|text|border)-(?:emerald|red|amber|blue|slate|purple|rose|cyan|indigo|teal|pink)-\d{2,3}\b"
    )
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        lines = len(source.splitlines())
        assert lines < 300, f"{path.name} is {lines} lines"
        assert "innerHTML" not in source, f"{path.name} builds markup from a string"
        assert "insertAdjacentHTML" not in source, f"{path.name} injects markup"
        assert "'undefined'" not in source, f"{path.name} probes for a global"
        assert not banned_colours.search(source), f"{path.name} hardcodes a colour class"
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), f"{path.name} hardcodes a hex colour"

    # The bar tints and the status fills resolve in places.css, from tokens.
    css = (ROOT / "ui" / "static" / "css" / "places.css").read_text(encoding="utf-8")
    for selector in (
        '.metric-bar-fill[data-hue="1"]',
        '.metric-column-fill[data-status="complete"]',
        '.metric-health[data-health="bad"]',
    ):
        assert selector in css, f"places.css does not resolve {selector}"
