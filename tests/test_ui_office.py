"""The Office place: one heading, a sized canvas, the pause overlay, and reuse.

Spec 6.2. The Office is the first place to host a second conversation surface,
so the assertion that matters most here is the one that keeps it from becoming
one: it opens the shared renderer or it opens nothing.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
OFFICE = JS / "places" / "office"

# The whole point of Phase 2A. Any of these appearing in office-place.js would
# mean a second transcript or composer had been built beside the shared one.
CONVERSATION_INTERNALS = (
    "BossModTranscript", "BossModMessage", "BossModComposer", "BossModEventCards",
    "createTranscript", "createComposer",
)


def _read(name: str) -> str:
    return (OFFICE / name).read_text(encoding="utf-8")


def test_office_registers_itself_and_meets_the_place_contract() -> None:
    """Registers itself, renders one focusable heading, and refreshes on resync.

    The shell does not drive Place.resync() yet, so the place subscribes to the
    topic itself. Without that the Office would sit on org-chart stats read
    before an outage and never say so — the silent staleness spec 1.4 exists to
    fix. The subscription goes into the disposer list, so it drains with the
    place rather than accumulating on every navigation.
    """
    source = _read("office-place.js")
    assert "BossModPlaces.register('office', BossModOfficePlace)" in source
    assert source.count("h('h1'") == 1, "navigate() focuses exactly one h1 per place"
    assert "h('h1', { tabindex: '-1' }, 'Office')" in source
    assert "disposers.push(ctx.bus.subscribe('resync', () => BossModOfficePlace.resync()))" in source
    resync = source.split("        resync() {", 1)[1].split("\n        },", 1)[0]
    assert "startCanvas()" in resync
    assert "orgView.refresh()" in resync


def test_office_sizes_the_canvas_on_mount() -> None:
    """A canvas built inside a not-yet-laid-out container measures zero.

    Two halves of one property: the place fires exactly one resize on entry,
    and the canvas still has only the single `panel-resize` path to receive it.
    """
    place = _read("office-place.js")
    canvas = _read("office-canvas.js")
    assert "window.dispatchEvent(new Event('panel-resize'))" in place
    assert place.count("dispatchEvent(new Event('panel-resize'))") == 1
    assert "addEventListener('panel-resize'" in canvas
    assert "addEventListener('resize'" not in canvas
    assert 'addEventListener("resize"' not in canvas
    # Switching back to the Map tab is the other moment the canvas is revealed
    # at a size it has never measured.
    assert "canvas.resize()" in place


def test_paused_shows_the_overlay_and_dims_the_floor() -> None:
    """store.runtimePaused drives both, so they can never disagree."""
    place = _read("office-place.js")
    css = (ROOT / "ui" / "static" / "css" / "places.css").read_text(encoding="utf-8")
    assert "const PAUSED_COPY = 'Everyone is paused';" in place
    assert "s.runtimePaused, paintRuntimeState" in place
    body = place.split("function paintRuntimeState(paused) {", 1)[1].split("\n    }", 1)[0]
    assert "stage.classList.toggle('is-paused', Boolean(paused))" in body
    assert ".office-paused" in body
    assert ".office-stage.is-paused .office-canvas" in css, "the dim class must be styled"


def test_desk_click_reuses_the_one_conversation_renderer() -> None:
    """A desk opens the shared conversation; it never builds a second one."""
    source = _read("office-place.js")
    assert "BossModConversation.createConversation(" in source
    assert "deskConversation.open(agentId, 'agent')" in source
    for internal in CONVERSATION_INTERNALS:
        assert internal not in source, (
            f"office-place.js names {internal}; the conversation surface is "
            f"BossModConversation's, not the Office's"
        )
    # The slide-over is the shared, focus-trapped one from core/overlays.js.
    assert "BossModOverlays.slideOver(" in source


def test_office_modules_stay_focused() -> None:
    """No monolith, and no markup built from strings.

    h() escapes by construction; agent names, room labels, and activity titles
    are all model or engine output flowing straight into these views.
    """
    files = sorted(OFFICE.glob("*.js"))
    assert files, "places/office holds no modules"
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = len(text.splitlines())
        assert lines <= 300, f"{path.name} is {lines} lines; the cap is 300"
        assert "innerHTML" not in text, f"{path.name} assigns innerHTML"
        assert "insertAdjacentHTML" not in text, f"{path.name} uses insertAdjacentHTML"
        # Dependencies arrive through deps. The optional-global guard is how a
        # missing script tag used to degrade silently instead of failing.
        assert "!== 'undefined'" not in text, (
            f"{path.name} guards a global with typeof; deps are injected, not sniffed"
        )
