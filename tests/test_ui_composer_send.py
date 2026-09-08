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


def test_chat_send_waits_for_ack_and_blocks_inflight() -> None:
    source = _read("conversation/composer.js")
    assert "BossModGates.createComposerSendGate()" in source
    submit = source.split("async function submit() {", 1)[1].split(
        "disposers.push(", 1
    )[0]
    # Re-pointed in the visual-parity pass: submit() now RETURNS the gate's
    # verdict so sendText() can report a blocked send instead of no-opping.
    # The property is unchanged — every send goes through the one gate.
    assert "return sendGate.submit(" in submit
    assert "const result = await submit();" in source
    # The gate clears the input after the ack; the composer never may.
    assert "el.value = ''" not in source
    assert "input.value = ''" not in source
    # A failed send has to reach the operator, not just the console.
    assert "onError:" in submit
    assert "setError(" in submit
    assert "BossModGates.setComposerError(" in source
    # The copy an unreachable agent produces, unchanged.
    assert "Failed to reach agent." in _read("conversation/sources/agent-source.js")
    apply_state = source.split("function applyState() {", 1)[1].split(
        "function setError(", 1
    )[0]
    assert "sendGate.busy()" in apply_state
    assert "sendBtn.disabled = !enabled" in apply_state
    assert "input.disabled = !enabled" in apply_state
    # applyState runs in the gate's finally, so a failure cannot strand it.
    assert "applyIdleState: applyState" in submit


def test_thread_send_surfaces_error_and_keeps_draft() -> None:
    composer = _read("conversation/composer.js")
    thread = _read("conversation/sources/thread-source.js")

    assert "BossModGates.createComposerSendGate()" in composer
    assert "'composer-error hidden'" in composer
    assert "role: 'alert'" in composer
    assert "BossModGates.setComposerError(" in composer
    assert "input.value = ''" not in composer

    send = thread.split("async function send(text) {", 1)[1].split(
        "function subscribe(on) {", 1
    )[0]
    # A rejected post must reject, not resolve: the gate keeps the draft only
    # when send() throws, and it must never be swallowed into a console.error.
    assert "throw new Error((await res.text())" in send
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
        "clearedOnSuccess": True,
        "blockedDoubleSubmit": True,
        "surfacedError": True,
    }
