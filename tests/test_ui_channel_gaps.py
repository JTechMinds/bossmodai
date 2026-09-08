"""Channel consent cards, thinking chrome, and hire in-flight helpers.

Phase 2A merged channels-view.js and channel-thread-dom.js into the one
conversation surface, so every assertion below was re-pointed by owner rather
than dropped: thread chrome and the archived list to the roster rail, the
archive copy to conversation/sources/thread-archive.js, live thread traffic to
conversation/sources/thread-source.js, consent cards to
conversation/event-cards.js, presence to conversation/transcript.js, and the
transcript cache to conversation/transcript.js + conversation/conversation.js.

Phase 2B split the rail in two, so the thread chrome, the Active/Archived
filter, and the creation copy now answer to shell/roster-threads.js; shell/
roster.js keeps People, search, and the assembly of both halves.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from core.agent_loop.dispatcher import _channel_id_for_presence

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HTML = ROOT / "ui" / "templates" / "index.html"
PRESENCE_HARNESS = Path(__file__).resolve().parent / "js_channel_presence_harness.cjs"
GATE_HARNESS = Path(__file__).resolve().parent / "js_inflight_gate_harness.cjs"
TRANSCRIPT_HARNESS = Path(__file__).resolve().parent / "js_transcript_harness.cjs"
ARCHIVE_HARNESS = Path(__file__).resolve().parent / "js_channel_archive_harness.cjs"
ARCHIVE_OPEN_TASKS_HARNESS = Path(__file__).resolve().parent / "js_archive_open_tasks_harness.cjs"

# The conversation stack, in the order the archive harness evaluates it.
CONVERSATION_STACK = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlays.js",
    JS / "conversation" / "empty-state.js",
    JS / "conversation" / "transcript.js",
    JS / "conversation" / "transcript-cache.js",
    JS / "conversation" / "message.js",
    JS / "conversation" / "event-cards.js",
    JS / "conversation" / "title-rename.js",
    JS / "conversation" / "chrome.js",
    JS / "conversation" / "composer.js",
    JS / "conversation" / "system-receipts.js",
    JS / "needs" / "needs-bar.js",
    JS / "conversation" / "sources" / "thread-archive.js",
    JS / "conversation" / "sources" / "thread-source.js",
    JS / "conversation" / "sources" / "agent-source.js",
    JS / "conversation" / "conversation.js",
]


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _script_sources() -> list[str]:
    return re.findall(r"static_url\('([^']+)'\)", HTML.read_text(encoding="utf-8"))


def test_operator_chrome_labels_threads() -> None:
    """'Threads' is the operator-facing word; 'channel' stays a backend term.

    The dock-era subjects (app.js, dock-manager.js, company-view.js, style.css)
    are on disk but unloaded, so those four assertions were false greens. The
    thread chrome is the roster rail's now — its Threads half after the Phase 2B
    split — and the consent-collapse rule belongs to conversation.css.
    """
    threads = _read("shell/roster-threads.js")
    places = _read("shell/places.js")
    css = (CSS / "conversation.css").read_text(encoding="utf-8")
    # Phase 2B split the rail; the Threads half took the chrome with it.
    assert "'Threads'" in threads
    # The creation copy is two states since the visual-parity pass, and it
    # moved to shell/thread-create.js when the polish round split making a
    # thread out of listing them. Round three moved the second state onto the
    # section header row, so `Create with N` became an icon-only confirm named
    # `Create thread` with the count in the header's middle slot. The rail
    # still owns the copy, which is what these lines have always guarded.
    creation = _read("shell/thread-create.js")
    assert "BossModThreadCreate.createThreadControls(" in threads
    assert "'New thread'" in creation
    assert "'Create thread'" in creation
    assert "${count} selected" in creation
    assert "start a shared thread" in creation
    # Threads are a roster section, not a seventh place.
    assert "channels" not in places.lower()
    assert ".host-path-consent-card.is-resolved" in css


def test_gates_and_consent_card_export_presence_and_consent_card() -> None:
    """Split by owner in Phase 2A: the gates moved to core/gates.js and the
    consent card to core/consent-card.js. Every asserted string survives."""
    gates = _read("core/gates.js")
    assert "function createChannelPresenceController(" in gates
    assert "createChannelPresenceController," in gates
    assert "function createInFlightGate(" in gates

    consent = _read("core/consent-card.js")
    assert "function renderHostPathConsentCard(" in consent
    assert "Allow once" in consent
    assert "Always allow (for all agents)" in consent
    assert "Always allowed (for all agents)" in consent
    assert "Deny" in consent
    assert "decision_note" in consent
    assert "{ label: 'Always allow'," not in consent
    assert "function collapseRelatedConsentCards(" in consent
    assert "is-resolved" in consent
    assert "collapseRelatedConsentCards," in consent


def test_no_notifications_tab_and_consent_stays_in_thread() -> None:
    places = _read("shell/places.js")
    cards = _read("conversation/event-cards.js")
    assert (
        "PLACE_IDS = Object.freeze(['chat', 'office', 'board', 'files', 'metrics', 'log'])"
        in places
    )
    assert "notifications" not in places.lower()
    # A consent ask is answered where it happened, never in a separate queue.
    assert "BossModConsentCard.renderHostPathConsentCard(" in cards
    assert "host-path-consent-card" in cards


def test_channels_view_renders_consent_card_and_member_thinking() -> None:
    """The largest re-point: one dock-era file's assertions, split by owner."""
    conversation = _read("conversation/conversation.js")
    transcript = _read("conversation/transcript.js")
    thread = _read("conversation/sources/thread-source.js")
    archive = _read("conversation/sources/thread-archive.js")
    cards = _read("conversation/event-cards.js")
    roster = _read("shell/roster.js")
    roster_threads = _read("shell/roster-threads.js")
    bus = _read("core/bus.js")

    # Presence: one shared model, painted by the transcript, driven by the source.
    assert "BossModGates.createChannelPresenceController()" in conversation
    assert "function renderPresence(" in transcript
    assert "presence.list(conversationId)" in transcript
    assert "is thinking..." in transcript
    assert "presence.start(" in thread
    assert "presence.stop(" in thread
    assert "presence.stopAll(" in thread

    # Consent cards render inline in the transcript.
    assert "BossModConsentCard.isHostPathConsentMessage(raw)" in thread
    assert "BossModConsentCard.renderHostPathConsentCard(" in cards
    assert "host-path-consent-card" in cards

    # A live message appends into the mounted transcript.
    assert "function append(" in transcript
    assert "on.message(toMessage(data))" in thread
    assert "transcript.append(message)" in conversation

    # Archive / Reopen chrome, and the archived list.
    assert "id: 'channel-archive-btn'" in thread
    assert "id: 'channel-reopen-btn'" in thread
    assert "Reopen" in thread
    assert ">Close<" not in thread
    assert ">Close<" not in roster
    assert ">Close<" not in roster_threads
    assert "channels-filter-active" in roster_threads
    assert "channels-filter-archived" in roster_threads
    assert "function isLiveThread(" in thread
    assert "function seal(" in thread
    assert "archive.prompt(open.count)" in thread
    # The branch belongs to spec(), not to its caller.
    assert "if (shouldPrompt(open.count))" not in thread

    # The archive copy, byte for byte.
    assert "function spec(count)" in archive
    assert "function copy(count)" in archive
    assert "Archive thread?" in archive
    assert "This thread has ${n} open tasks. Sealing stops new posts and access cards." in archive
    assert "Hides it from the active list and seals the room" in archive
    assert "no new messages or access cards" in archive
    assert "Open tasks stay on the board." in archive
    assert "Cancel tasks & archive" in archive
    assert "Archive only" in archive
    assert "channel-archive-back" in archive
    assert "channel-archive-confirm" in archive
    assert "channel-archive-cancel-tasks" in archive
    assert "HONESTY_COPY" in archive
    assert (
        "Not permanently deleted — leaves the active list and seals the room (no new posts)."
        in archive
    )
    assert "cancel_open_tasks=true" in archive
    assert "Archive this thread?" not in archive

    assert "Threads" in roster_threads
    roster_create = _read("shell/thread-create.js")
    assert "'New thread'" in roster_create
    assert "'Create thread'" in roster_create
    # The rail still assembles both halves, and the Threads half assembles the
    # creation controls, so nothing in the chain is orphaned.
    assert "BossModRosterThreads.createThreads(" in roster
    assert "BossModThreadCreate.createThreadControls(" in roster_threads

    # channel_presence is routed to the thread that owns it. The dock-era
    # switch in app.js and the AgentContext delegation it called are gone; the
    # bus and the source carry the topic now.
    assert "'channel_presence'," in bus
    assert "bus.subscribe('channel_presence'" in thread


def test_live_channel_message_appends_without_loading_remount() -> None:
    source = _read("conversation/sources/thread-source.js")
    handler = source.split("bus.subscribe('channel_message', (data) => {", 1)[1].split(
        "bus.subscribe('channel_presence'", 1
    )[0]
    assert "on.message(" in handler
    assert "isLiveThread(" in handler
    assert handler.index("isLiveThread(") < handler.index("on.message(")
    assert "Loading thread..." not in handler
    assert "Loading channel..." not in handler
    # Nothing in the live path reloads or remounts the transcript.
    assert "load()" not in handler
    assert "setStatus" not in handler


def test_thread_switch_keeps_shell_and_uses_cache() -> None:
    """The Part B debt, discharged.

    The load-order half lost its subject when the dock scripts were dropped and
    was temporarily reduced to "the module is defined". Its real subject is the
    transcript, which owns the cache, and the conversation controller, which
    consumes it.
    """
    source = _read("conversation/conversation.js")
    assert "cache.recall(" in source
    assert "createCache()" in _read("conversation/transcript-cache.js")
    # The cache must be defined before its consumer runs — and it borrows
    # messageKey from the view, so the view has to come before it in turn.
    sources = _script_sources()
    assert sources.index("js/conversation/transcript.js") < sources.index(
        "js/conversation/transcript-cache.js"
    )
    assert sources.index("js/conversation/transcript-cache.js") < sources.index(
        "js/conversation/conversation.js"
    )


def test_channel_archive_harness_stays_clickable_after_keep_shell_switch() -> None:
    result = subprocess.run(
        ["node", str(ARCHIVE_HARNESS)] + [str(path) for path in CONVERSATION_STACK],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "archiveHandoffEnabled": True,
        "keepShellSwitchEnabled": True,
        "sameButton": True,
    }


def test_archive_open_tasks_harness_covers_prompt_branches() -> None:
    """The eleven properties, re-pointed by owner: the copy and both prompt
    branches to sources/thread-archive.js, archive/reopen/seal to
    sources/thread-source.js, and the archived list to shell/roster-threads.js,
    the half of the rail that owns the thread list after the Phase 2B split."""
    result = subprocess.run(
        [
            "node",
            str(ARCHIVE_OPEN_TASKS_HARNESS),
            str(JS / "core" / "agent-status.js"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "avatar.js"),
            str(JS / "core" / "store.js"),
            str(JS / "core" / "bus.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "core" / "consent-card.js"),
            str(JS / "core" / "overlays.js"),
            str(JS / "core" / "format.js"),
            str(JS / "shell" / "roster-row-meta.js"),
            str(JS / "conversation" / "sources" / "thread-archive.js"),
            str(JS / "conversation" / "sources" / "thread-source.js"),
            str(JS / "shell" / "roster-people.js"),
            str(JS / "shell" / "thread-create.js"),
            str(JS / "shell" / "roster-threads.js"),
            str(JS / "shell" / "roster.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "cancelAndArchive": True,
        "archiveOnly": True,
        "zeroOpenTwoButtons": True,
        "openTasksThreeButtons": True,
        "zeroOpenConfirm": True,
        "zeroOpenCancelAborts": True,
        "backAborts": True,
        "archivedNotLive": True,
        "honestyCopy": True,
        "archivedFilterLists": True,
        "reopenUnseals": True,
    }


def test_transcript_cache_swaps_without_loading() -> None:
    """The cache's four properties, re-pointed from channel-thread-dom.js.

    keepsShell and cachesTranscript are proven behaviourally by the transcript
    harness; swapsChrome moved to the chrome view and is proven by the archive
    harness's sameButton; noLoadingFlash is the cached-paint-before-loading
    ordering in the controller.
    """
    result = subprocess.run(
        [
            "node",
            str(TRANSCRIPT_HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "avatar.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "core" / "format.js"),
            str(JS / "conversation" / "empty-state.js"),
            str(JS / "conversation" / "transcript.js"),
            str(JS / "conversation" / "transcript-cache.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["cacheCopies"] is True
    assert payload["dedupes"] is True

    body = _read("conversation/conversation.js").split(
        "async function open(conversationId, kind) {", 1
    )[1]
    assert body.index("if (cached) paint(cached);") < body.index(
        "transcript.setStatus('loading')"
    )
    assert "Loading thread..." not in body
    assert "Loading channel..." not in body


def test_channel_presence_helper_only_tracks_channel_turns() -> None:
    assert _channel_id_for_presence({
        "type": "channel_message",
        "channel_id": "ch-1",
    }) == "ch-1"
    assert _channel_id_for_presence({
        "type": "channel_response",
        "channel_id": "  ch-2  ",
    }) == "ch-2"
    assert _channel_id_for_presence({"type": "human_chat", "channel_id": "ch-1"}) is None
    assert _channel_id_for_presence({"type": "channel_message"}) is None


def test_channel_presence_harness_tracks_members_per_channel() -> None:
    result = subprocess.run(
        ["node", str(PRESENCE_HARNESS), str(JS / "core" / "gates.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "tracksMembers": True,
        "isolatesChannels": True,
        "stopsOne": True,
        "stopsAll": True,
    }


def test_inflight_gate_harness_blocks_nested_submit() -> None:
    result = subprocess.run(
        ["node", str(GATE_HARNESS), str(JS / "core" / "gates.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "blocksNested": True,
        "clearsAfter": True,
        "allowsLater": True,
    }
