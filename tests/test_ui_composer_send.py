"""UI A4 — chat/meeting composers keep drafts until send is acknowledged."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_composer_send_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_gates_export_composer_send_gate() -> None:
    source = _read("core/gates.js")
    assert "function createComposerSendGate()" in source
    assert "function setComposerError(" in source
    assert "createComposerSendGate," in source
    assert "setComposerError," in source


def test_chat_send_stays_editable_while_a_send_is_queued() -> None:
    source = _read("conversation/composer.js")
    assert "BossModGates.createComposerSendGate()" in source
    submit = source.split("async function submit() {", 1)[1].split(
        "disposers.push(", 1
    )[0]
    # Every send goes through the one gate, which returns its verdict.
    assert "await sendGate.submit(" in submit
    assert "return result;" in submit
    assert "const result = await submit();" in source
    # The gate takes the accepted line out of the box. The composer never may.
    assert "el.value = ''" not in source
    assert "input.value = ''" not in source
    # A failed send has to reach the operator, not just the console.
    assert "onError:" in submit
    assert "onQueued: setQueued" in submit
    assert "setError(" in submit
    assert "BossModGates.setComposerError(" in source
    assert "'composer-hint hidden'" in source
    # The copy an unreachable agent produces, unchanged as the fallback.
    assert "Failed to reach agent." in _read("conversation/sources/agent-source.js")
    apply_state = source.split("function applyState() {", 1)[1].split(
        "function setQueued(", 1
    )[0]
    # Thinking and an in-flight send are not a lock. No model and a sealed
    # thread still disable the field.
    assert "sendGate.busy()" not in apply_state
    assert "const enabled = hasUsableModel && allowed;" in apply_state
    assert "sendBtn.disabled = !enabled" in apply_state
    assert "input.disabled = !enabled" in apply_state
    assert "applyIdleState: applyState" in submit


def test_thread_send_surfaces_error_and_keeps_draft() -> None:
    composer = _read("conversation/composer.js")
    thread = _read("conversation/sources/thread-source.js")

    assert "BossModGates.createComposerSendGate()" in composer
    assert "'composer-error hidden'" in composer
    assert "role: 'alert'" in composer
    assert "BossModGates.setComposerError(" in composer
    assert "input.value = ''" not in composer

    send = thread.split("async function send(text, attachmentIds) {", 1)[1].split(
        "function subscribe(on) {", 1
    )[0]
    # A rejected post must reject, not resolve: the gate puts the text back
    # only when send() throws. The reason is the server's, not raw JSON.
    assert "throw new Error(await refusal(res, 'Could not post to this thread.'))" in send
    assert "Could not post to this thread." in send
    assert "console.error" not in send


def test_composer_send_harness_keeps_draft_and_blocks_double_submit() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "gates.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "keptDraftOnFailure": True,
        "leftNewerDraftIntact": True,
        "clearedOnSuccess": True,
        "queuedSecondSend": True,
        "stayedEnabled": True,
        "surfacedError": True,
    }


def test_gate_allows_empty_text_with_attachments() -> None:
    """Regression: pasting an image then hitting Send must not be blocked by the gate's empty-draft check."""
    source = _read("core/gates.js")
    # The gate must accept a hasPayload flag that bypasses the !draft block.
    assert "hasPayload" in source
    assert "!draft && !hasPayload" in source


def test_composer_passes_has_payload_to_gate() -> None:
    """Regression: the composer must tell the gate that attachments are pending."""
    composer = _read("conversation/composer.js")
    submit = composer.split("async function submit() {", 1)[1].split(
        "disposers.push(", 1
    )[0]
    assert "hasPayload: pendingAttachments.length > 0" in submit


def test_sources_include_attachment_ids_in_payload() -> None:
    """Regression: send() must forward attachment_ids to the API, not drop them."""
    agent = _read("conversation/sources/agent-source.js")
    thread = _read("conversation/sources/thread-source.js")
    assert "async function send(text, attachmentIds) {" in agent
    assert "payload.attachment_ids = attachmentIds" in agent
    assert "async function send(text, attachmentIds) {" in thread
    assert "payload.attachment_ids = attachmentIds" in thread


def test_attach_button_not_gated_by_text_validation() -> None:
    """Regression: the attach button must be clickable regardless of input text.

    The file picker opens on click; it must not be behind a 'type a message'
    validation gate.
    """
    composer = _read("conversation/composer.js")
    # The attach button's onclick just triggers the file input — no text check.
    attach_block = composer.split("const attachBtn = h('button', {", 1)[1].split("}, h('i'", 1)[0]
    assert "fileInput.click()" in attach_block
    assert "input.value" not in attach_block
    assert "canSend" not in attach_block
