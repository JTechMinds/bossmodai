"""The four operator requests from the second hands-on run.

Round two's lesson stands: a structural assertion pins a mechanism so it cannot
silently regress, and it cannot tell you the result looks right. Each test below
says which of the two it is doing, and the ones that can be proven on built
nodes are proven there rather than by reading source — "what a click does" is
not a property a grep can see.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
CONVERSATION = JS / "conversation"
HERE = Path(__file__).resolve().parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """The declaration block of exactly one rule.

    Anchored on a newline and a following `{`, so `.modal-panel` never picks up
    `.modal-panel[data-size="wide"]` and `.conversation-title` never picks up
    `.conversation-title-edit`.
    """
    opener = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert opener, f"no rule for {selector}"
    return css[opener.end():].split("}", 1)[0]


def _run(harness: str, modules: list[Path]) -> dict:
    """Run a Node harness over an explicit module list and read its payload."""
    args = ["node", str(HERE / harness)] + [str(path) for path in modules]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _app_js() -> list[Path]:
    return [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.parts]


# ─── Task 4 (backend): renaming one thread ───


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _a_thread(client: TestClient) -> str:
    agent = db.create_agent("Debrah", role="PM")
    created = client.post(
        "/api/channels", headers=_headers(), json={"agent_ids": [agent.id]},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_a_thread_can_be_renamed() -> None:
    """The name the operator types is the name the rail reads back.

    Asserted through a re-read rather than off the PATCH response, because the
    response could echo the request without ever reaching the database.
    """
    client = _client()
    channel_id = _a_thread(client)

    patched = client.patch(
        f"/api/channels/{channel_id}",
        headers=_headers(),
        json={"name": "Release triage"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Release triage"

    renamed = client.get(f"/api/channels/{channel_id}", headers=_headers()).json()
    assert renamed["channel"]["name"] == "Release triage"
    # Renaming is metadata: it does not touch the roster or seal the room.
    assert renamed["channel"]["status"] == "active"
    assert renamed["channel"]["member_count"] == 1


def test_rename_trims_the_name_it_stores() -> None:
    """Leading and trailing space is invisible in the rail and sorts wrong."""
    client = _client()
    channel_id = _a_thread(client)
    patched = client.patch(
        f"/api/channels/{channel_id}", headers=_headers(), json={"name": "  Standup  "},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Standup"


def test_rename_rejects_an_empty_name() -> None:
    """A thread with a blank name is unfindable in the rail."""
    client = _client()
    channel_id = _a_thread(client)
    assert client.patch(
        f"/api/channels/{channel_id}", headers=_headers(), json={"name": "   "},
    ).status_code == 400


def test_rename_rejects_a_name_longer_than_the_cap() -> None:
    """A rail row is one line; an unbounded name is a row nobody can read."""
    client = _client()
    channel_id = _a_thread(client)
    over = "x" * 121
    assert client.patch(
        f"/api/channels/{channel_id}", headers=_headers(), json={"name": over},
    ).status_code == 400
    assert client.patch(
        f"/api/channels/{channel_id}", headers=_headers(), json={"name": "x" * 120},
    ).status_code == 200


def test_rename_404s_on_an_unknown_channel() -> None:
    assert _client().patch(
        "/api/channels/nope", headers=_headers(), json={"name": "x"},
    ).status_code == 404


# ─── Task 3 (backend): one batch "last human-chat message" query ───


def test_the_world_carries_the_last_human_chat_time_per_agent() -> None:
    """The rail's timestamp column has nothing to read without this.

    It is the DIRECT human chat specifically: an agent-to-agent message is work
    the operator never sees in that conversation, and counting it would put a
    time on a row nobody has spoken in.
    """
    import db.crud
    import db.messages as messages

    chatty = db.create_agent("Debrah", role="PM", desk_x=1, desk_y=1)
    quiet = db.create_agent("Jim", role="Eng", desk_x=3, desk_y=1)
    peer = db.create_agent("Joey", role="Writer", desk_x=5, desk_y=1)

    earlier = messages.create_message("__human__", chatty.id, "morning", message_type="human")
    latest = messages.create_message(chatty.id, "__human__", "morning back", message_type="human")
    # Both land in the same second otherwise, which would make "the latest
    # wins" true by accident rather than by the MAX.
    db.crud.execute(
        "UPDATE messages SET created_at = $1 WHERE id = $2",
        [earlier.created_at - timedelta(days=3), earlier.id],
    )
    backdated = messages.get_human_chat_thread(chatty.id)[0]
    assert backdated.created_at < latest.created_at, "the backdate must land"
    # Agent-to-agent traffic is not the operator's conversation with anyone.
    messages.create_message(peer.id, quiet.id, "peer note", message_type="work")
    # Neither is durable work output, which has no recipient at all.
    messages.create_message(quiet.id, None, "a work artifact", message_type="work")

    rows = {row["id"]: row for row in db.get_world_state()}
    assert set(rows) == {chatty.id, quiet.id, peer.id}
    # Both directions count, and the LATEST of them wins.
    assert rows[chatty.id]["lastMessageAt"] == latest.created_at
    assert rows[chatty.id]["lastMessageAt"] != backdated.created_at
    # Nobody has spoken to these two: null, never a fabricated date.
    assert rows[quiet.id]["lastMessageAt"] is None
    assert rows[peer.id]["lastMessageAt"] is None


def test_the_last_message_time_is_one_query_not_one_per_agent() -> None:
    """N+1 on the roster is exactly what this exists to avoid.

    Counted through db/world.py's own `query` binding, so the number is the
    module's, not the whole call stack's: `heal_desk_seats()` runs its own
    reads and is not the subject.
    """
    import db.messages as messages
    import db.world as world

    for index in range(5):
        agent = db.create_agent(f"Agent {index}", role="Eng", desk_x=1 + index, desk_y=1)
        messages.create_message("__human__", agent.id, "hello", message_type="human")

    calls: list[str] = []
    original = world.query

    def counting(sql, params=None):
        calls.append(sql)
        return original(sql, params)

    world.query = counting
    try:
        rows = db.get_world_state()
    finally:
        world.query = original

    assert len(rows) == 5
    assert all(row["lastMessageAt"] is not None for row in rows)
    # The roster, and the batch. Not the roster plus one read per agent.
    assert len(calls) == 2, calls


# ─── Task 3 (formatter): absolute times that read off the operator's clock ───


def _format_payload() -> dict:
    return _run("js_format_time_harness.cjs", [JS / "core" / "format.js"])


def test_timestamps_are_absolute_and_disambiguate_the_year() -> None:
    """Absolute, not relative — the operator's decision.

    The year is this plan's addition to it: without it a message from last
    September and one from this September render identically, and "Sep 2" on a
    row nobody has touched in fourteen months is a lie of omission.
    """
    payload = _format_payload()
    assert payload["today"] == "10:10 AM"
    assert payload["thisYear"] == "Sep 2"
    assert payload["lastYear"] == "Sep 2, 2025"
    # A conversation nobody has spoken in shows nothing, not a fake date.
    assert payload["never"] == ""
    # ...and neither does a stored value that cannot be parsed.
    assert payload["unparseable"] == ""


def test_the_formatter_reads_the_operators_clock_not_utc() -> None:
    """The one that bites, proven across +14 and -11 as well as UTC.

    A message at 23:30 tonight must read as a time; one at 00:30 this morning
    must too, which is where a UTC date comparison goes wrong on either side of
    the meridian; and last night's 23:30, read after midnight, must read as a
    date, which is what stops "always a time" from passing.
    """
    payload = _format_payload()
    assert payload["respectsLocalMidnight"] is True, payload["midnightDetail"]
    zones = [row["zone"] for row in payload["midnightDetail"]]
    assert "Pacific/Kiritimati" in zones and "Pacific/Niue" in zones, zones


# ─── Task 1: the thread select mode explains itself ───

# The order tests/js_roster_harness.cjs evaluates its modules in. A third copy
# of the list, for the reason the second one carries: three files drive the same
# rail, and a shared constant would hide which of them a breakage belongs to.
ROSTER_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "roster-row-meta.js",
    JS / "shell" / "roster-people.js",
    JS / "shell" / "thread-create.js",
    JS / "shell" / "thread-view-menu.js",
    JS / "shell" / "roster-threads.js",
    JS / "shell" / "roster.js",
]


def _roster_payload() -> dict:
    return _run("js_roster_harness.cjs", ROSTER_MODULES)


def test_select_mode_lives_entirely_in_the_section_header() -> None:
    """One row, three slots, two states — proven on the built rail.

    The mode used to be announced on a hint line and left through a
    `Create with N` / `Cancel` row, both of them below the filters and neither
    of them near the `+` that opened it. Both rows are gone; the middle slot
    of the header says what is picked, and the action group swaps in place.

    Round four emptied the idle slot: the invitation it carried truncated at
    rail width and explained a mode nobody was in. The property here — the
    mode lives ENTIRELY on this row — is unchanged, so the assertion follows
    the slot to what it says now.
    """
    payload = _roster_payload()
    # Idle: one control, and nothing in the middle slot.
    assert payload["idleHeaderActions"] == ["New thread"]
    assert payload["idleMiddleSlot"] == ""
    # Selecting: cancel and confirm, and the count appears in the slot.
    assert payload["selectingHeaderActions"] == ["Cancel", "Create thread"]
    assert payload["selectingMiddleSlotAtOne"] == "1 selected"
    # The separate row is gone.
    assert payload["hasStandaloneCreateRow"] is False
    # ...and so is its stylesheet, so nothing can render one by accident.
    css = _read(CSS / "shell.css")
    for retired in (".roster-select-actions", ".roster-thread-hint",
                    ".roster-create-thread", ".roster-thread-cancel"):
        assert retired not in css, retired


def test_the_confirm_is_disabled_with_nothing_selected() -> None:
    """Entering the mode does not immediately offer an impossible action."""
    payload = _roster_payload()
    assert payload["confirmDisabledAtZero"] is True
    # ...and one agent is a valid thread; POST /api/channels accepts it.
    assert payload["confirmEnabledAtOne"] is True


def test_escape_leaves_select_mode() -> None:
    """It matches every other dismissible state in the shell.

    And it drains: the rail owns the listener, so a mount/unmount cycle that
    left it behind would leave a dead handler holding the whole rail alive.
    """
    payload = _roster_payload()
    assert payload["escapeCancelsSelectMode"] is True
    assert payload["escapeListenerDrains"] is True

    # Escape belongs to the innermost surface first, which is the rule
    # shell/shortcuts.js already follows for the same reason.
    create = _read(JS / "shell/thread-create.js")
    guard = create.split("function onDocumentKeydown(", 1)[1].split("\n        }", 1)[0]
    assert "role=\"dialog\"" in guard, guard
    assert "isSelecting()" in guard, guard


# ─── Task 3 (rails): the timestamp column ───


def test_both_rails_render_the_last_activity_time() -> None:
    """Read off the BUILT rail, because the claim is about what renders.

    The fixture gives one person a message at 10:10 this morning, the other
    none, and the thread a post from 2020 — so the three branches of the
    formatter are exercised through the rail rather than in isolation, and a
    row with nothing to say is shown to carry no column at all.
    """
    payload = _roster_payload()
    assert payload["personTimestamps"] == ["10:10 AM"]
    # The day is the reader's local one, so only the year is asserted here;
    # js_format_time_harness.cjs owns the calendar rules.
    assert payload["threadTimestamps"][0].endswith(", 2020")
    assert payload["quietRowHasNoMeta"] is True


def test_the_two_rails_share_one_right_hand_column() -> None:
    """Both halves grew the same column at the same time.

    Two copies of it is how the People list and the Threads list end up with
    right edges that do not line up — the defect the shared avatar was
    extracted to fix on the other side of the row.
    """
    definers = sorted(
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "roster-row-meta" in _read(path)
    )
    assert definers == ["shell/roster-row-meta.js"], definers
    for half in ("shell/roster-people.js", "shell/roster-threads.js"):
        assert "BossModRosterRowMeta.rowMeta(" in _read(JS / half), half
    # The world payload's field survives normalisation, or the rail reads
    # undefined on every row.
    assert "lastMessageAt: w.lastMessageAt || null" in _read(JS / "core/agent-status.js")


# ─── Task 2: the agent form is a centred modal ───

# The order tests/js_context_harness.cjs evaluates its modules in. A third copy
# of tests/test_ui_context.py's list, on the same reasoning the second one
# carries: every file that drives the column reads it, and a shared constant
# would hide which of them a breakage belongs to.
CONTEXT_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "specialty.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "title-rename.js",
    CONVERSATION / "chrome.js",
    CONVERSATION / "composer.js",
    CONVERSATION / "system-receipts.js",
    JS / "needs" / "need-shape.js",
    JS / "needs" / "needs-store.js",
    JS / "needs" / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation.js",
    JS / "shell" / "places.js",
    JS / "places" / "files" / "file-content.js",
    JS / "places" / "files" / "file-form.js",
    JS / "places" / "files" / "file-ops.js",
    JS / "places" / "files" / "file-viewer.js",
    JS / "context" / "mini-office.js",
    JS / "context" / "desk-opener.js",
    JS / "context" / "desk-files.js",
    JS / "context" / "desk-notes.js",
    JS / "context" / "desk-tasks.js",
    JS / "context" / "desk-actions.js",
    JS / "context" / "agent-api.js",
    JS / "context" / "agent-templates-api.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-connections.js",
    JS / "context" / "agent-form-bindings.js",
    JS / "context" / "agent-form-hydrate.js",
    JS / "context" / "agent-form.js",
    JS / "context" / "agent-submit.js",
    JS / "context" / "agent-recovery.js",
    JS / "context" / "agent-form-save.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "pack-card.js",
    JS / "marketplace" / "filter-rail.js",
    JS / "context" / "agent-template-picker.js",
    JS / "context" / "agent-form-template.js",
    JS / "context" / "agent-dialog-footer.js",
    JS / "context" / "agent-edit.js",
    JS / "context" / "desk-panel.js",
    JS / "context" / "context-column.js",
    JS / "places" / "chat" / "chat-place.js",
]


def _context_payload() -> dict:
    return _run("js_context_harness.cjs", CONTEXT_MODULES)


AGENT_FORM_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "avatar.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-connections.js",
    JS / "context" / "agent-submit.js",
]


def _agent_form_payload() -> dict:
    return _run("js_agent_form_harness.cjs", AGENT_FORM_MODULES)


def test_one_modal_implementation_with_a_size_variant() -> None:
    """The wide dialog is a VARIANT, not a second modal.

    createModal was built for a short question with two buttons; the agent
    form is tall enough to need a scrolling body. Forking it would have been
    the quick way and would have left two focus traps, two Esc handlers, and
    two ideas of what returning focus means — which is the duplication
    core/overlays.js exists to have removed.
    """
    overlays = _read(JS / "core/overlays.js")
    definers = sorted(p.relative_to(JS).as_posix() for p in _app_js()
                      if "function createModal(" in _read(p))
    assert definers == ["core/overlays.js"], definers
    assert "size" in overlays.split("function createModal(", 1)[1][:400]

    css = _read(CSS / "overlays.css")
    # Centred in the viewport, and wider than the confirm dialog it shares an
    # implementation with.
    panel = _rule(css, ".modal-panel")
    assert "position: fixed" in panel
    assert "translate(-50%, -50%)" in panel
    wide = _rule(css, '.modal-panel[data-size="wide"]')
    assert "width:" in wide
    # The BODY scrolls; the title and the action row are pinned outside it.
    body = _rule(css, ".modal-body")
    assert "overflow-y: auto" in body
    assert "min-height: 0" in body, "a flex child will not scroll without it"
    assert "flex" in panel and "column" in panel


def test_the_wide_modal_keeps_the_shared_keyboard_contract() -> None:
    """A scrolling body is exactly where a focus trap leaks.

    The trap walks `element.querySelectorAll(FOCUSABLE)`, so a body of inputs
    is the case the confirm dialog never exercised — proven at the overlay
    level, where the behaviour lives.
    """
    payload = _run("js_overlays_harness.cjs",
                   [JS / "core/dom.js", JS / "core/overlay-focus.js", JS / "core/overlays.js"])
    assert payload["wideModalIsMarked"] is True
    assert payload["wideModalTrapsTabAcrossItsBody"] is True
    assert payload["wideModalEscCloses"] is True
    assert payload["wideModalRestoresFocus"] is True
    # The action row is not inside the scroller, or it would scroll away.
    assert payload["wideModalActionsSitOutsideTheBody"] is True


def test_both_agent_flows_open_the_centred_modal() -> None:
    """Hire and Edit are the same dialog — the operator's decision.

    Proven on built nodes: the column hosted the form for one of the two and
    a source check would not notice if only one flow had moved.
    """
    edit = _read(JS / "context/agent-edit.js")
    assert "BossModOverlays.createModal(" in edit
    assert "size: 'wide'" in edit

    # The context column no longer hosts a form, and the state that reached it
    # is gone rather than left reachable.
    column = _read(JS / "context/context-column.js")
    assert "AgentEdit" not in column, column
    assert "if (mode === 'desk' && !agentId)" not in column
    place = _read(JS / "places/chat/chat-place.js")
    assert "placeParams.hire" not in place

    payload = _context_payload()
    assert payload["editOpensTheWideModal"] is True
    assert payload["hireOpensTheWideModal"] is True
    assert payload["modalIsAttachedToTheBodyNotTheColumn"] is True
    # The desk comes back when the dialog closes, rather than the column being
    # left holding whatever was there before.
    assert payload["closingTheModalRestoresTheDesk"] is True
    # The rail's Hire row is reachable while a desk's Edit dialog is up, and
    # two stacked wide modals fight over Escape and the focus trap.
    assert payload["onlyOneDialogAtATime"] is True


def test_the_form_lost_no_fields_in_the_move() -> None:
    """The move is a container change. Every field must still be built."""
    payload = _agent_form_payload()
    for field in ("name", "role", "description", "color", "desk",
                  "connections", "prompt_history"):
        assert payload["formFields"][field] is True, field
    assert payload["colourClampStillEnforced"] is True
    assert payload["editFlowStillOffersRemove"] is True
    # ...and hiring offers no Delete, because there is nothing to delete yet.
    assert payload["hireFlowOffersNoRemove"] is True


# ─── Task 4: rename a thread inline ───

# The order tests/js_conversation_harness.cjs evaluates its modules in.
CONVERSATION_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "title-rename.js",
    CONVERSATION / "chrome.js",
    CONVERSATION / "composer.js",
    CONVERSATION / "system-receipts.js",
    JS / "needs" / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation.js",
]


def _conversation_payload() -> dict:
    return _run("js_conversation_harness.cjs", CONVERSATION_MODULES)


def test_the_title_is_editable_without_looking_like_a_link() -> None:
    """Easy but non-obvious — the operator's requirement.

    No blue, no underline, no affordance at rest: at rest it is the title, and
    the edit state is the feedback.

    The spelling has moved twice and the property has not. Round four turned
    the rest state's `border: 0` into a reserved transparent hairline (nothing
    visible, and nothing moves when something appears) and the edit state's
    `--accent-bg` fill into a soft blue text colour. This round took the blue
    out too: the text going blue while you typed it was the last accent in the
    row, and it read as a state the title had entered rather than as a field
    you were in. The dotted hairline is the whole of the edit state now, in the
    same token and the same texture the field's focus indicator uses — one
    event, told once.
    """
    css = _read(CSS / "conversation.css")
    rule = _rule(css, ".conversation-title-edit")
    # No link costume at rest.
    assert "text-decoration" not in rule
    assert "var(--accent)" not in rule
    assert "border: 1px dotted transparent" in rule
    assert "background: transparent" in rule
    # ...and it inherits the title's type rather than declaring its own, so the
    # two cannot drift into looking like different things.
    assert "font: inherit" in rule
    # FIXED width. The field used to set its own `size` from its value on every
    # paint, so the header re-flowed on every keystroke — and the two controls
    # that confirm the edit sit beside it now, so they moved with it.
    assert "width: 24ch" in rule
    assert "sizeToValue" not in _read(CONVERSATION / "title-rename.js")
    # A name past the box ellipsises at rest rather than clipping mid-glyph.
    assert "text-overflow: ellipsis" in rule
    # Editing IS the feedback, and it is the hairline and nothing else.
    editing = _rule(css, '.conversation-title-edit[data-editing="true"]')
    assert "dotted" in editing
    assert "var(--line-control)" in editing
    assert "var(--accent)" not in editing

    chrome = _read(CONVERSATION / "chrome.js") + _read(CONVERSATION / "title-rename.js")
    # Keyboard reachable, and Enter opens it (SC 2.1.1).
    assert "'Rename this thread'" in chrome

    payload = _conversation_payload()
    assert payload["titleOpensEditOnEnter"] is True
    assert payload["escapeCancelsRenameWithoutSaving"] is True
    assert payload["saveActionAppearsBesideArchive"] is True
    # Agent conversations are not renameable — only threads.
    assert payload["agentTitleIsNotEditable"] is True
    # ...and neither is a sealed room, which takes no other write either.
    assert payload["archivedThreadIsNotRenameable"] is True


def test_a_rename_reaches_the_server_before_the_title_changes() -> None:
    """Not optimistic: the title is the server's answer, not the draft."""
    payload = _conversation_payload()
    assert payload["renamePatchesTheChannel"] is True
    assert payload["renameShowsTheSavedName"] is True


def test_a_half_typed_rename_does_not_follow_a_conversation_switch() -> None:
    """apply() leaves the field alone while it is being edited — that is what
    stops a live repaint from stealing the operator's typing, and it is exactly
    what would carry one thread's draft onto the next one without a reset."""
    assert _conversation_payload()["renameDoesNotFollowASwitch"] is True
    controller = _read(CONVERSATION / "conversation.js")
    assert "chrome.reset();" in controller
    assert "reset() {" in _read(CONVERSATION / "chrome.js")


def test_a_failed_rename_says_so_and_keeps_the_text() -> None:
    """The operator's typing is never discarded by a failed request."""
    payload = _conversation_payload()
    assert payload["failedRenameReportsError"] is True
    assert payload["failedRenameKeepsDraft"] is True
    # ...and it stays in edit mode, so Save is still there to try again.
    assert payload["failedRenameStaysInEditMode"] is True
