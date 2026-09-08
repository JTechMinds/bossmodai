"""The needs-you queue: one shape, one resolution path, six quiet surfaces."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
NEEDS = JS / "needs"
HARNESS = Path(__file__).resolve().parent / "js_needs_harness.cjs"

# The order the needs harness evaluates its modules in.
NEEDS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    # needs-store.js guards refresh() with the shared load generation, so the
    # harness loads the real gates module rather than a second copy of it.
    JS / "core" / "gates.js",
    JS / "core" / "format.js",
    JS / "conversation" / "event-cards.js",
    NEEDS / "need-shape.js",
    NEEDS / "needs-store.js",
    NEEDS / "needs-popover.js",
    NEEDS / "needs-bar.js",
    NEEDS / "needs-toast.js",
]

# GET /api/needs speaks snake_case. Nothing downstream of the normaliser may.
WIRE_KEYS = ("agent_id", "agent_name", "created_at", "conversation_id")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _harness() -> dict:
    args = ["node", str(HARNESS)] + [str(path) for path in NEEDS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_needs_store_normalises_to_camel_case() -> None:
    """Consumers read camelCase; exactly one file knows the wire shape.

    needs/need-shape.js is that file — normalising is its whole job, so it is
    the one exemption. Every other consumer naming a snake_case key would mean
    the conversion had leaked, which is how three renderers ended up each
    knowing their own wire format the last time.
    """
    consumers = [
        JS / "shell" / "roster.js",
        # The People half reads store.needs since the rail was split.
        JS / "shell" / "roster-people.js",
        JS / "shell" / "header.js",
    ]
    consumers += [path for path in sorted(NEEDS.glob("*.js")) if path.name != "need-shape.js"]
    for path in consumers:
        source = _read(path)
        for key in WIRE_KEYS:
            assert key not in source, f"{path.relative_to(JS)} reads the wire key {key}"

    shape = _read(NEEDS / "need-shape.js")
    for key in WIRE_KEYS:
        assert key in shape, f"need-shape.js must be the file that converts {key}"
    assert "agentId" in shape and "conversationId" in shape


def test_needs_harness() -> None:
    assert _harness() == {
        "ok": True,
        "normalisesShape": True,
        "quietOnUnchangedTick": True,
        "restoresOnFailedResolve": True,
        "keepsQueueOnFailedRefresh": True,
        "errorNeedFromDiagnostic": True,
        "ignoresUnknownActivity": True,
        "popoverShowsResolutionFailure": True,
        "barToggleNeverHidesTheBell": True,
        "escClosesAndReturnsFocus": True,
        "showMeKeepsShell": True,
        "suppressionRuleHolds": True,
        "noToastOnFirstSnapshot": True,
        "resyncIsABaselineNotArrivals": True,
        "unchangedBaselineStillEnds": True,
        "inspectionDoesNotResolve": True,
        "barLeavesConsentInline": True,
        "targetsNavigate": True,
    }


def test_needs_never_polls() -> None:
    """Spec 5.3: the trigger set is complete, so a timer would only mask a gap."""
    source = _read(NEEDS / "needs-store.js")
    assert "setInterval" not in source
    assert "setTimeout" not in source
    # The three things that do drive a refresh, named positively.
    assert "bus.subscribe('activity'" in source
    assert "bus.subscribe('resync'" in source
    assert "bus.subscribe('diagnostic'" in source


def test_failed_resolution_restores_the_need() -> None:
    """The one place in this phase where a swallowed error is a safety problem."""
    source = _read(NEEDS / "needs-store.js")
    body = source.split("async function resolve(need, action) {", 1)[1]
    catch = body.split("} catch (err) {", 1)[1].split("errorNeeds.delete", 1)[0]

    assert "const before = current.slice();" in body
    # The restore publishes the PRE-removal list, with the failure attached.
    assert "publish(before.map(" in catch
    assert "error: (err && err.message) || 'Could not resolve.'" in catch
    assert "console.error('[needs] resolution failed', err);" in catch
    assert "throw err;" in catch
    # Forced, so a restore is published even when the signature is unchanged;
    # without it a failure on a need that was never removed would be silent.
    assert "), true);" in catch
    assert catch.index("publish(before.map(") < catch.index("throw err;")


def test_refresh_drops_a_superseded_response() -> None:
    """A stale queue read must never overwrite a newer one.

    refresh() has four callers - boot, resync, a resolution, and every matching
    activity broadcast - and `status_changed` fires on each agent lifecycle
    transition, so overlapping reads are routine. Without a load generation the
    response that resolves LAST wins rather than the one issued last, and the
    queue can settle on an older snapshot until some later event dislodges it.
    Every other async loader in this codebase uses this gate.
    """
    source = _read(NEEDS / "needs-store.js")
    assert "BossModGates.createLoadGeneration()" in source
    body = source.split("async function refresh() {", 1)[1].split("\n        }", 1)[0]
    assert "const loadId = refreshLoad.next()" in body
    assert "if (!refreshLoad.isCurrent(loadId)) return;" in body
    # The guard must sit between the await and the assignment; before the await
    # it is always current, and after the assignment the damage is done.
    assert body.index("await api(") < body.index("if (!refreshLoad.isCurrent(loadId)) return;")
    assert body.index("if (!refreshLoad.isCurrent(loadId)) return;") < body.index(
        "serverNeeds = rows.map(normalise)"
    )
    # A superseded read's failure must not claim the error line either.
    assert body.count("if (!refreshLoad.isCurrent(loadId)) return;") == 2


def test_needs_modules_stay_focused() -> None:
    """The cap moved tree-wide in Phase 4; what stays here is needs/'s own.

    Every need title and sub-line is server text about agent activity — a
    folder an agent asked to read, a command it wants to run — so none of it
    may become markup, and none of these modules may reach for a global rather
    than take `api` from ctx.
    """
    for path in sorted(NEEDS.rglob("*.js")):
        source = _read(path)
        assert "innerHTML" not in source, f"{path.name} builds markup from a string"
        assert "insertAdjacentHTML" not in source, f"{path.name} injects markup"
        assert "typeof BossMod" not in source, f"{path.name} probes for a global"
        assert "apiFetch" not in source, f"{path.name} must take api from ctx"


def test_popover_actions_come_from_the_server() -> None:
    """Spec 5.2: the client never hardcodes a resolution URL per kind.

    That is what makes a fifth need kind a backend-only change. A literal
    /api/ path in this file would be that promise broken.
    """
    source = _read(NEEDS / "needs-popover.js")
    assert "/api/" not in source, "the popover must not name an API path"
    # The whole action travels through to the store, which reads href/method.
    # The popover never takes either apart, so it cannot build a URL of its own.
    assert "needs.resolve(need, action)" in source
    assert "action.href" not in source
    assert "action.method" not in source
    # The label and the tone are the server's too, so a fifth kind needs no
    # copy and no styling here.
    assert "}, action.label)" in source
    assert "${action.tone}" in source

    store_source = _read(NEEDS / "needs-store.js")
    assert "api(action.href, { method: action.method })" in store_source


def test_bell_is_never_suppressible() -> None:
    """Spec 5.5: the bar is dismissible, the bell is the one guaranteed path."""
    popover = _read(NEEDS / "needs-popover.js")
    header = _read(JS / "shell" / "header.js")

    # The footer control writes exactly one flag, and it is the bar's.
    assert "store.setState({ needsBarEnabled:" in popover
    assert "needsBarDismissed" not in popover

    # Nothing anywhere writes a flag that would hide the bell.
    for path in sorted(NEEDS.glob("*.js")) + sorted((JS / "shell").glob("*.js")):
        source = _read(path)
        for flag in ("bellEnabled", "bellDismissed", "bellHidden", "hideBell", "needsBellEnabled"):
            assert flag not in source, f"{path.relative_to(JS)} writes {flag}"

    # The header renders the bell unconditionally, with no branch above it.
    # The rail toggle joined the row ahead of the brand in the visual-parity
    # pass; the property is unchanged and only the spelling of the append moved.
    assert "errorEl, bellLive, bell, pause, gear" in header
    assert "if (" not in header.split("el.append(railToggle, brand, nav,", 1)[1].split("));", 1)[0]


def test_toast_and_bar_are_mutually_exclusive() -> None:
    """Spec 5.5, enforced by test rather than left to judgement.

    Looking at the conversation, the bar tells you quietly; not looking at it,
    the toast comes to you. One need can never produce both, or six surfaces
    become the noise problem they were built to solve.
    """
    payload = _harness()
    assert payload["suppressionRuleHolds"] is True
    # A consent ask is already inline in the transcript of the very
    # conversation the bar is scoped to, so the bar leaves it there.
    assert payload["barLeavesConsentInline"] is True


def test_no_toast_on_the_first_snapshot() -> None:
    """Launching with a backlog must not throw the backlog at the operator.

    The same holds after a resync: what comes back is a re-read of what was
    already true, not a set of arrivals.
    """
    payload = _harness()
    assert payload["noToastOnFirstSnapshot"] is True
    assert payload["resyncIsABaselineNotArrivals"] is True
    # The flag is cleared in recompute(), not in publish(): publish returns
    # early on an unchanged signature, so a resync that changed nothing would
    # otherwise leave the baseline armed and swallow the next real arrival.
    assert payload["unchangedBaselineStillEnds"] is True

    # Only the queue can tell a reload from an arrival, so only the queue
    # decides. The toast never diffs store.needs itself.
    toast = _read(NEEDS / "needs-toast.js")
    store_source = _read(NEEDS / "needs-store.js")
    assert "needs.subscribeArrivals(" in toast
    assert "store.subscribe(" not in toast
    assert "baselinePending" in store_source
    assert "baselinePending = true;" in store_source.split("bus.subscribe('resync'", 1)[1]


def test_bar_renders_through_event_cards() -> None:
    """One renderer, one appearance per need (spec 4.3)."""
    source = _read(NEEDS / "needs-bar.js")
    assert "BossModEventCards.renderEventCard(" in source
    # No second look for the same information: every class this file builds
    # belongs to the strip itself, never to a card.
    built = re.findall(r"class: ['\"`]([^'\"`]+)", source)
    assert built, "the bar builds no elements at all"
    for name in built:
        assert name.startswith("needs-bar"), (
            f"needs-bar.js builds its own card markup: class {name!r}"
        )


def test_no_source_emits_progress_or_event_kinds() -> None:
    """A source that emits one of the bar's kinds would render it twice.

    event-cards.js owns the visuals for every non-message row; only
    needs-bar.js produces `event`. The throw on an unknown kind and this
    assertion are what catch a source that forgets.

    Phase 4 deleted `progress` outright (spec 12, carried items) — it had a
    renderer and never had a producer, and its information now lives in the
    presence row. The source-side ban stays: a source emitting it would now hit
    the "no renderer" throw rather than paint a second answer to "is it still
    working?".
    """
    sources = JS / "conversation" / "sources"
    for path in sorted(sources.glob("*.js")):
        source = _read(path)
        assert "kind: 'progress'" not in source, f"{path.name} emits a bar kind"
        assert "kind: 'event'" not in source, f"{path.name} emits a bar kind"

    cards = _read(JS / "conversation" / "event-cards.js")
    for kind in ("'event'", "'request'", "'note'"):
        assert f"message.kind === {kind}" in cards
    assert "message.kind === 'progress'" not in cards
    # An unrecognised tone must not paint as a neutral row.
    assert 'no treatment for event tone' in cards
    assert 'no renderer for kind' in cards


def test_an_inspection_action_decides_nothing() -> None:
    """"Open task" and "Open diagnostics" are GETs. They settle nothing.

    Removing the entry for one would tell the operator they had resolved
    something they had only looked at. The exception is an error need, which is
    held client-side: the server list never contained it, so reading the
    diagnostic is the only thing that can ever clear it.
    """
    assert _harness()["inspectionDoesNotResolve"] is True

    source = _read(NEEDS / "needs-store.js")
    resolve = source.split("async function resolve(need, action) {", 1)[1]
    assert "const decides = action.method !== 'GET';" in resolve
    assert "if (decides) {" in resolve
    # The optimistic removal is the only thing behind that branch; the request,
    # the rollback, and the refresh all still run for a GET.
    guarded = resolve.split("if (decides) {", 1)[1].split("}", 1)[0]
    assert "publish(current.filter(" in guarded
    assert "await api(" not in guarded
    assert "errorNeeds.delete(need.id);" in resolve


def test_need_targets_come_from_one_mapping_table() -> None:
    """Hard part 5: no per-kind branch at any call site.

    Phase 2B's `blocked` and `error` needs performed the server-described GET
    and refreshed, because Board and Log did not exist. They do now, so those
    inspections become navigations — and the destination comes from ONE table
    in need-shape.js. Scattered `if (kind === 'blocked')` branches are how four
    surfaces end up with four opinions about where a blocked task lives.
    """
    shape = _read(NEEDS / "need-shape.js")
    assert "const KIND_TARGETS = Object.freeze({" in shape
    assert "place: 'board', params: { taskId: need.id }" in shape
    assert "place: 'log', params: { diagnosticId: need.id }" in shape
    assert "consent: (need) => chatTarget(need.conversationId, need.agentId)" in shape
    assert "approval: (need) => chatTarget(need.conversationId, need.agentId)" in shape
    assert "function targetFor(need)" in shape
    # Both normalisers attach it, so no consumer has to ask for one.
    assert shape.count("need.target = targetFor(need);") == 2

    # No call site decides anything from a kind.
    for name in ("needs-popover.js", "needs-bar.js", "needs-toast.js"):
        source = _read(NEEDS / name)
        for kind in ("'blocked'", "'error'", "'consent'", "'approval'"):
            assert f"kind === {kind}" not in source, f"{name} branches on kind"
            assert f"kind == {kind}" not in source, f"{name} branches on kind"
        assert "place: 'board'" not in source, f"{name} names a place id of its own"
        assert "place: 'log'" not in source, f"{name} names a place id of its own"
    # The bar still groups by kind for the CARD TONE, which is presentation and
    # is a frozen table of its own, not a branch.
    bar = _read(NEEDS / "needs-bar.js")
    assert "const BAR_CARDS = Object.freeze({" in bar
    assert "need.target" in bar

    # The server-described actions are untouched: `target` is a client concern.
    store_source = _read(NEEDS / "needs-store.js")
    assert "const decides = action.method !== 'GET';" in store_source
    assert "target" not in store_source, "the queue store must not know about targets"


def test_blocked_and_error_needs_navigate_to_their_place() -> None:
    """Behavioural, through the harness: the target actually takes you there.

    A source-level table proves the mapping exists; only this proves the button
    uses it, carries the id into placeParams, and leaves the server-described
    GET alone — `inspectionDoesNotResolve` still has to pass beside it.
    """
    payload = _harness()
    assert payload["targetsNavigate"] is True
    assert payload["inspectionDoesNotResolve"] is True
    assert payload["showMeKeepsShell"] is True


def test_reaching_a_conversation_never_remounts_chat() -> None:
    """Every route into a conversation obeys shell/roster.js's rule.

    Navigation is only how the operator REACHES Chat; the store is what
    switches the conversation. Navigating while already there remounts the
    place and takes the transcript cache, the composer draft, and the caret
    with it. The roster has always known this; the popover's "Show me" and the
    toast's "Open" are two more ways in, and both would have remounted.
    """
    assert _harness()["showMeKeepsShell"] is True

    toast = _read(NEEDS / "needs-toast.js")
    assert "store.getState().place !== 'chat'" in toast, (
        "needs-toast.js navigates to Chat without checking whether it is already open"
    )
    assert "navigate('chat');\n" not in toast.replace(
        "if (store.getState().place !== 'chat') navigate('chat');", ""
    )

    # Phase 3B generalised the popover: a need now leads to its own place, so
    # the check is no longer spelled against the literal 'chat'. The property is
    # unchanged and now covers every destination — the ONE place with a draft,
    # a transcript cache and a caret to lose is never re-navigated to, while
    # Board and Log carry what to show in their params and so always are.
    popover = _read(NEEDS / "needs-popover.js")
    go_to = popover.split("function goTo(target) {", 1)[1].split("\n        }", 1)[0]
    assert "const alreadyThere = store.getState().place === target.place;" in go_to
    assert "if (alreadyThere && target.place === 'chat') return;" in go_to
    assert "navigate(target.place, target.params);" in go_to
    assert "navigate('chat')" not in popover
    # It closes before it navigates, so focus lands on the new place rather
    # than being pulled back to the bell it just left.
    assert go_to.index("close();") < go_to.index("navigate(target.place, target.params);")
