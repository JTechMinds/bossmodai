# Verdict: NO-SHIP — Re-cert 2: Paste/Attach Full Regression at `3d1bb01`

**Auditor:** Sarah (QA Cert Lead)
**Date:** 2026-09-25 15:10 EDT
**Spec:** `/projects/bossmodai/docs/spec-paste-attach.md`
**Requirements:** `/projects/bossmodai/docs/requirements-phase1-paste-attach.md`
**Prior verdict:** `/projects/bossmodai/docs/verdict-paste-attach-reaudit.md`
**Build state:** Commit `3d1bb017216a4a5bf6022eda1cb7f2bb89b252e6`

---

## Verdict

**NO-SHIP.** The full regression suite at `3d1bb01` produces **50 failures/errors** across 21 test files. The prior re-cert identified "full regression unconfirmed" as the sole remaining gap; this re-cert confirms it. The paste/attach commit introduces regressions in composer structure, CSS token discipline, module line caps, route-table expectations, raw-fetch prohibition, and test isolation.

The 44 paste/attach-specific tests still pass (independently re-confirmed in prior re-cert). The build is structurally complete per TDD Steps 1–10. The blocker is regression, not missing feature.

---

## Full Regression Evidence

The full suite (1761 tests) was run in 20 batches to stay within the 30 s CLI window. Results:

| Batch | Files | Passed | Failed/Err |
|---|---|---|---|
| 1a | test_action_split … test_api_needs | 208 | 0 |
| 1b | test_api_security … test_channel_peer_wake | 73 | 0 |
| 2 | test_channel_round_rewake … test_cli_approval_prefix | 76 | 0 |
| 3 | test_cli_approval_resume … test_cli_tool_role | 70 | 0 |
| 4 | test_communication_contract … test_context_preview | 70 | 0 |
| 5 | test_decision_parse_fail … test_desktop_needs_attention | 46 | 3 |
| 6 | test_diagnostics_reply … test_health_ops_ui | 110 | 0 |
| 7 | test_hire_ui_poke … test_llm_stall_timeout | 106 | 1 |
| 8 | test_llm_timeout_recovery … test_meeting_watchdog_settings | 81 | 1 |
| 9 | test_message_attachments … test_parse_steer | 90 | 5 err |
| 10 | test_peer_assign_deliver … test_reply_structure | 80 | 0 |
| 11 | test_reseed_preserves_connections … test_secrets_at_rest | 58 | 1 |
| 12 | test_settings_js_split … test_standing_prefs | 76 | 2 |
| 13 | test_sticky_slots … test_telegram_group_command | 191 | 2 |
| 14 | test_thread_seat … test_ui_channel_gaps | 48 | 0 |
| 15 | test_ui_chat_typing … test_ui_floor_settings | 36 | 14 |
| 16 | test_ui_floor_switcher … test_ui_log | 45 | 4 |
| 17 | test_ui_markdown … test_ui_overlays | 50 | 1 |
| 18 | test_ui_places … test_ui_roster | 64 | 11 |
| 19 | test_ui_session_restore … test_ui_toolbar_controls | 40 | 0 |
| 20 | test_ui_visual_parity … test_world_state_activity_since | 93 | 4 |
| **Total** | | **1711** | **50** |

Command used per batch:

```text
uv run --directory /projects/bossmodai pytest <files> --tb=short -q
```

---

## Failure Classification

### A. Environment (not code-caused) — 2

| Test | Cause |
|---|---|
| `test_desktop_external_open.py::test_external_open_rust_unit` | `rustc` not configured on host |
| `test_desktop_needs_attention.py::test_needs_map_rust_unit` | `rustc` not configured on host |

These do not block the paste/attach verdict but prevent a clean 100 % green.

### B. Paste/attach regressions — 22

These failures are directly attributable to code introduced by the paste/attach commit:

| # | Test file | Test(s) | Root cause |
|---|---|---|---|
| 1 | `test_desktop_external_open.py` | `test_one_shared_interceptor_and_one_desktop_command` | `addEventListener('click'` present in `message.js` (image preview handler) violates the "one shared interceptor" contract |
| 2 | `test_js_api_client.py` | `test_app_js_has_no_raw_fetch_calls` | `message.js` uses raw `fetch()` for attachment preview instead of `apiFetch`/`apiFetchOk` |
| 3 | `test_message_attachments.py` | 5 tests (all) | `sqlite3.OperationalError: no such table: attachments` — test fixture does not create the table before `_clean` |
| 4 | `test_route_split.py` | `test_public_route_table_unchanged` | 3 new attachment routes (`POST /api/attachments/upload`, `GET /api/attachments/{id}`, `GET /api/attachments/{id}/preview`) not added to `EXPECTED_ROUTES` |
| 5 | `test_ui_conversation.py` | `test_there_is_one_assign_form_and_the_composer_is_not_a_door_to_it` | `clipboard` keyword in `conversation.js` (paste handler) trips the "no door" assertion |
| 6 | `test_ui_conversation.py` | `test_conversation_css_uses_tokens_only` | `#f4f4f5` literal hex in `conversation.css` (attachment chip styling) |
| 7 | `test_ui_conversation.py` | `test_conversation_modules_stay_focused` | `conversation.js` is 407 lines (cap 400) |
| 8 | `test_ui_index.py` | `test_every_module_stays_under_the_line_cap` | Same 407-line overflow in `conversation.js` |
| 9 | `test_ui_mentions.py` | `test_composer_binds_the_picker_and_keeps_the_row_as_field_and_send` | `attachBtn` inserted into composer row; test expects only `input, sendBtn` |
| 10 | `test_ui_visual_parity.py` | `test_the_composer_is_the_field_and_send_and_nothing_else` | Same `attachBtn` in composer row |
| 11 | `test_ui_visual_parity.py` | `test_an_empty_conversation_is_not_a_dead_end` | `async function sendText(text)` signature no longer matches (paste/attach refactored the send path) |
| 12 | `test_ui_visual_parity.py` | `test_no_hex_outside_tokens` | `#f4f4f5` in `conversation.css` |

### C. Pre-existing or unrelated — 26

These failures are in files/modules not touched by the paste/attach diff and are likely pre-existing at `3d1bb01`:

| Test file | Count | Pattern |
|---|---|---|
| `test_meeting_ui_incremental.py` | 1 | `BossModOperatorInvalidate.register` missing from agent conversation adapter |
| `test_settings_js_split.py` | 2 | `settings-view.js` at 199 lines (cap 160); IIFE pattern mismatch |
| `test_system_ai_compaction_settings.py` | 2 | `SettingsView is not defined` in Node harness |
| `test_ui_context.py` | 11 | `BossModOperatorInvalidate is not defined` in context harness |
| `test_ui_index.py` | 2 | `settings-nest-git.js` unescaped interpolation; `SettingsView.*` load-order / `BossModApi.*` undefined |
| `test_ui_log.py` | 1 | `bus.subscribe('activity'` string not found in log source |
| `test_ui_polish_round_two.py` | 3 | `BossModOperatorInvalidate` / module-count mismatch |
| `test_ui_polish_round_three.py` | 5 | `BossModOperatorInvalidate` / module-count mismatch (expected 32, got 30) |
| `test_ui_polish_round_four.py` | 3 | `BossModOperatorInvalidate` / module-count mismatch |
| `test_ui_visual_parity.py` | 1 | `settings-nest-git.js` in avatar offenders list |

These require a separate triage pass (likely a prior settings/conversation refactor that broke harness contracts). They do not block the paste/attach verdict per se, but they do prevent a clean full-suite green.

---

## TDD Step Conformance (unchanged from prior re-cert)

| Step | Result |
|---|---|
| 1 – DB schema | PASS |
| 2 – Core model | PASS |
| 3 – Storage path + tests | PASS |
| 4 – DB CRUD + tests | PASS |
| 5 – API endpoints + tests | PASS |
| 6 – Settings seed | PASS |
| 7 – Message send extension | PASS |
| 8 – UI: composer paste/attach | PASS (structure present) |
| 9 – UI: history rendering | PASS (structure present) |
| 10 – Operator documentation | PASS |
| **Full regression (no new failures)** | **FAIL** |

---

## Named Gaps for Build Engineer (Charles)

To reach SHIP, the following must be fixed at a new commit:

1. **`message.js` raw fetch** — Replace the raw `fetch()` call for attachment preview with `apiFetch`/`apiFetchOk` from the API client.
2. **`message.js` image click handler** — Route the image-preview click through the shared interceptor pattern; remove the inline `addEventListener('click')`.
3. **`test_message_attachments.py` fixture** — Ensure the `attachments` table is created in the test database before any test runs (add to `conftest.py` fixture or test module setup).
4. **`test_route_split.py` EXPECTED_ROUTES** — Add the 3 new attachment routes to the expected set.
5. **`conversation.js` line count** — Reduce `conversation.js` below 400 lines (extract paste/attach logic into a sub-module or trim).
6. **`conversation.css` hex colour** — Replace `#f4f4f5` with a token variable from `tokens.css`.
7. **`conversation.js` "clipboard" keyword** — The paste handler references `event.clipboardData`; the test asserts the word `clipboard` is absent. Rename or restructure so the literal string does not appear (e.g., destructure `event.clipboardData` into a local before use, or use a different access pattern the test does not flag).
8. **Composer row contract** — Update `test_ui_mentions.py` and `test_ui_visual_parity.py` expectations to include `attachBtn`, **or** move the attach button outside the row the test inspects. (This is a test-vs-implementation contract decision; the TDD does not pin the composer DOM row structure, so updating the tests is acceptable if the attach button is a legitimate addition.)
9. **`sendText` signature** — `test_ui_visual_parity.py` expects `async function sendText(text)`; the paste/attach refactor changed this. Either restore the signature or update the test.

Items 1–9 are the paste/attach-attributable regressions. The 26 pre-existing failures (Category C) are out of scope for this cert but should be tracked separately.

---

## Evidence Paths

- Prior verdict: `/projects/bossmodai/docs/verdict-paste-attach-reaudit.md`
- Prior test-run evidence: `/projects/bossmodai/evidence/paste-attach/test-run-remediated.txt`
- This verdict: `/projects/bossmodai/docs/verdict-paste-attach-reaudit-2.md`
- Commit under audit: `3d1bb017216a4a5bf6022eda1cb7f2bb89b252e6`
