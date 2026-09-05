# BossMod AI — Health backlog

Ordered work list from [`HEALTH_AUDIT.md`](HEALTH_AUDIT.md). Each item is meant to be **one PR**. Do not combine a security P0 with a JS split.

**Legend:** P0 = correctness / security / data-loss. P1 = high-leverage health. P2 = cleanup.  
**Already on `main` (do not redo):** PR #1 (audit ancestor), **PR #2** (SEC-P0-01 / SEC-P0-02 — fail-closed Telegram + local API token + redaction). Those are live, not open.

**ID prefix:** `HA-` (health audit) so these do not collide with `SEC-P0-*` in `docs/AUDIT_P0_P1.md`. Where an item continues that audit, the old ID is listed under **Alias**.

---

## Sequence at a glance

| Order | ID | Title | Sev | Area |
| ---: | --- | --- | --- | --- |
| — | *(PR #2, shipped)* | Fail-closed Telegram + local API token + redaction | P0 | security |
| 1 | HA-SEC-P0-04 | Narrow company-files root; hide backups | P0 | security |
| 2 | HA-SEC-P0-03 | Shell path jail + dangerous seed rules | P0 | security |
| 3 | HA-TEST-P1-01 | Restore critical-path pytest module | P1 | tests |
| 4 | HA-CORR-P0-02 | Telegram CLI approve must resume the agent | P0 | correctness |
| 5 | HA-CORR-P0-03 | Skip-turn must not exhaust the trigger | P0 | correctness |
| 6 | HA-CORR-P0-01 | Reused tasks must wake the assignee | P0 | correctness |
| 7 | HA-SEC-P1-04 | Policy: `xargs` / shells / argv[0] | P1 | security |
| 8 | HA-SEC-P1-01 | CLI output must not be `role=system` | P1 | security |
| 9 | HA-PROD-P1-01 | Assign Task UI + reuse outcome in API | P1 | product |
| 10 | HA-CORR-P1-02 | Task status transition table | P1 | correctness |
| 11 | HA-CORR-P1-03 | Watchdog covers accepted/waiting | P1 | correctness |
| 12 | HA-CORR-P1-04 | Telegram `/meeting` vs room meetings | P1 | correctness |
| 13 | HA-SEC-P1-03 | Unique approval IDs | P1 | security |
| 15 | HA-OPS-P1-02 | Offline UI (vendor Tailwind or honest README) | P1 | ops |
| 16 | HA-STRUCT-P1-01 | Split `api/routes.py` | P1 | structure |
| 17 | HA-STRUCT-P1-02 | Split `actions.py` handlers | P1 | structure |
| 18 | HA-STRUCT-P1-03 | Split `loop.py` / `decision_runtime.py` | P1 | structure |
| 19 | HA-STRUCT-P1-04 | Split settings + CLI-policy JS | P1 | structure |
| 20 | HA-STRUCT-P1-05 | Split `managed_writer.py` + `context_builder.py` preview | P1 | structure |
| 21 | HA-STRUCT-P1-06 | Remove `core` → `api.websocket` import | P1 | DI |
| 22 | HA-STRUCT-P1-07 | Dedup meeting/channel rounds | P1 | DRY |
| 23 | HA-SEC-P1-05 | Secret-at-rest plan (keychain / encrypt) | P1 | security |
| 24 | HA-SEC-P1-02 | Trigger leases / heartbeat | P1 | correctness |
| 25 | HA-LOOP-P1-07 | Seed meeting watchdog settings | P1 | ops |
| 26 | HA-SEC-P1-06 | Simulator default dry-run | P1 | security |
| 27 | HA-CORR-P1-05 | Reset-runtime + skip-turn task hygiene | P1 | correctness |
| 28 | HA-CORR-P1-06 | Handle `clarify_ambiguous_match`; don’t create a duplicate | P1 | correctness |
| 29 | HA-CORR-P1-07 | Dispatch `task_assigned` while another activity is live | P1 | correctness |
| 30 | HA-TEST-P1-02 | Policy + path-jail regression tests | P1 | tests |
| 31 | HA-TEST-P1-03 | Task/meeting/channel pytest slice | P1 | tests |
| 32 | HA-STRUCT-P1-08 | Shared JS API client (finish PR #2 migration) | P1 | DRY |
| 33 | HA-SEC-NEW-01 | Connection-test URL allowlist | P2 | security |
| 34 | HA-OPS-P2-01 | Drop unused `duckdb` / `twilio` | P2 | ops |
| 35 | HA-OPS-P2-02 | Desktop `pkill` → recorded PID | P2 | ops |
| 36 | HA-OPS-P2-03 | README stack / token / offline copy | P2 | product |
| 37 | HA-CORR-P2-01 | Safe `config.get_int` | P2 | correctness |
| 38 | HA-STRUCT-P2-01 | Stop growing `db/__init__.py` | P2 | DI |
| 39 | HA-PROD-P2-01 | Chat empty-reply UX | P2 | product |

---

## Already shipped (not in this backlog)

### PR #2 — SEC-P0-01 / SEC-P0-02

- **Problem:** Telegram fail-open; unauthenticated REST/WS return secrets and expose reseed/simulator.
- **Status:** **Merged on `main`.** Empty allowlist is deny-all and the bot will not start; `/api` REST and WebSocket require `X-BossMod-Token` (or `Authorization: Bearer`); settings/connections redact secrets. Unauthenticated `POST /api/settings/reseed` → 401.
- **Do not treat as open.** JS call sites send the token via `apiFetch` (HA-STRUCT-P1-08). Residual first-run UX is HA-OPS-P1-01 (banner / send-disabled), not missing auth.

---

## Backlog items

### HA-SEC-P0-04 — Narrow company-files root

| | |
| --- | --- |
| **Severity** | P0 |
| **Area** | security / files |
| **Alias** | SEC-P0-04 |

**Problem.** Company browser and mutators resolve against `artifacts/` (`api/routes.py` `_resolve_safe_company_path` + `artifacts_root()`). `reset_database()` writes SQLite copies to `artifacts/db_backups/`. UI can list/read/delete those backups (secrets at rest).

**Why it matters.** One click in Company → Files after a reseed leaks every API key. Delete is data-loss.

**Approach.** Serve `artifacts/projects` (or a dedicated `company/` root) as the browser root. Keep `db_backups/` and `artifacts/agents/` outside it. Deny `*.bak`, `*.sqlite3`, `*.db` even if someone later widens the root. Reuse `filesystem.resolve_relative_path` instead of a second helper.

**Acceptance**

- [x] `GET /api/company/files?path=/` lists `projects` contents (or only project dirs), not `db_backups` or raw `agents`.
- [x] `GET /api/company/files/raw?path=/db_backups/<file>` → 400/404.
- [x] Delete/rename of a backup path fails.
- [x] Existing project files still open/edit.
- [x] Pytest for the three path cases (no need to run a browser).

---

### HA-SEC-P0-03 — Shell path jail + seed lockdown

| | |
| --- | --- |
| **Severity** | P0 (latent until `cli_shell_enabled=true`) |
| **Area** | security / bm_cli |
| **Alias** | SEC-P0-03 (seed part overlaps HA-SEC-P1-04) |

**Problem.** `execute_shell_command` runs with `cwd=workspace` but accepts absolute paths. Seed `always_allowed` includes interpreters and `xargs`. Approved commands skip policy entirely.

**Why it matters.** Turning shell on (or approving one command) is a host escape.

**Approach (this PR — no bubblewrap required):**

1. Reject argv path-like tokens that resolve outside the agent workspace / projects mount.
2. Move `python` / `python3` / `node` / `xargs` off `always_allowed` (approval or never).
3. Add `sh`, `bash`, `zsh`, `dash` to `never_allowed`.
4. Keep `cli_shell_enabled` default false.
5. `execute_approved_command` still runs the approved argv **but** still applies the path jail (approval is not a jailbreak).

OS sandboxes (landlock/bwrap) can be a follow-up PR if needed.

**Acceptance**

- [x] `cat /etc/passwd` denied by path jail even if policy would allow `cat`.
- [x] Seed rules: no interpreter/`xargs` in `always_allowed`; shells in `never_allowed`.
- [x] Existing seed users: migration or “seed defaults” button updates dangerous rows (document the choice).
- [x] Tests in `tests/test_cli_policy.py` (can land with HA-TEST-P1-02 if this PR is too fat — prefer tests here).

**Shipped.** Path jail in `execute_shell_command` (also applied by `execute_approved_command`). Interpreters (`python` / `python3` / `node`), `xargs`, and POSIX shells (`sh` / `bash` / `zsh` / `dash`) are `never_allowed` — not `approval_required` — because `-c` / `-e` payloads and `xargs` exec multiplexing are invisible to the path jail. Existing DBs are hardened on `init_db` via `reconcile_hardened_seed_rules()`; Settings → CLI Policy → Seed defaults still wipes and re-inserts `_SEED_RULES`. `cli_shell_enabled` default remains `false`. argv[0] basename matching is left to HA-SEC-P1-04.

---

### HA-TEST-P1-01 — Restore critical-path pytest module

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | tests |
| **Alias** | TEST-P1-08 (partial) |

**Problem.** `scripts/run_runtime_smoke_suite.sh` references `tests/test_agent_runtime.py::…` — file is missing. `main` has 42 lines of tests.

**Why it matters.** Every later split/security PR is flying blind. The script is a footgun (`uv run` that file fails).

**Approach.** Recreate a **small** `tests/test_agent_runtime.py` with fakes (no live LLM):

1. Human chat, no model → skip, no crash.
2. `create_or_bind_task` + `task_assigned` trigger row exists.
3. `execute_action` `done` with missing deliverable → `world_feedback`, task not complete.
4. `resolve_relative_path` rejects `../`.
5. Fix or delete the smoke script so it matches reality.

Do **not** try to replay all 11 historical names in one PR if fixtures are heavy — 4–6 tests is enough.

**Acceptance**

- [x] `uv run pytest -q` on a clean checkout is green.
- [x] Smoke script either runs those tests or is removed.
- [x] Tests use `BOSSMOD_DB_PATH` temp DB (conftest already does).

**Shipped.** Restored `tests/test_agent_runtime.py` with a small no-LLM critical-path slice (human-chat skip, `create_or_bind_task` + `task_assigned` row, `done` missing-deliverable `world_feedback`, `resolve_relative_path` traversal). `scripts/run_runtime_smoke_suite.sh` now runs that module instead of 11 missing historical node-ids.

---

### HA-CORR-P0-01 — Reused tasks must wake the assignee

| | |
| --- | --- |
| **Severity** | P0 |
| **Area** | correctness / tasks |

**Problem.** `POST /api/tasks` only enqueues `task_assigned` when `create_or_bind_task` returns `create_new_task`. Bind/reuse is silent. Response does not include `outcome`.

**Why it matters.** Duplicate assign looks successful and the agent never runs.

**Approach.** If the bound task is open and assigned, enqueue (or coalesce) a `task_assigned` / `task_follow_up` trigger. Return `{ task, outcome }` (additive JSON is OK if UI doesn’t exist yet). Same rule if a future Assign Task UI calls this endpoint.

**Acceptance**

- [x] Re-POST same title/assignee → trigger row queued or already-open trigger updated.
- [x] Response body includes `outcome` (`create_new_task` \| `bind_existing_task` \| `clarify_ambiguous_match`).
- [x] Pytest in the module from HA-TEST-P1-01.

**Shipped.** `POST /api/tasks` wakes an open assigned task on create **and** bind/reuse via `assignment_wake_trigger` (`task_assigned`). Response is `{ task, outcome }`. A second assign coalesces an already-queued `task_assigned` row (same helper as follow-up/update). Ambiguous-match handling is tracked under HA-CORR-P1-06.

---

### HA-CORR-P0-02 — Telegram CLI approve must resume the agent

| | |
| --- | --- |
| **Severity** | P0 |
| **Area** | correctness / Telegram / CLI |

**Problem.** `cmd_approve` and `handle_approval_callback` persist approve/reject and reply in Telegram. They never create `cli_approval_resolved` or wake the worker. Desktop `POST /api/cli-policy/approvals/{id}/approve` does create the trigger but via `db.create_agent_trigger` (no `runtime_services.enqueue_trigger` / `wake_dispatcher`). `expire_stale_cli_approval_requests` is never called.

**Why it matters.** Telegram buttons look successful; the agent stays paused. Desktop resume can sit until the next accidental wake.

**Approach.** One helper `resume_cli_approval(request_id, *, approved, note)` used by API and Telegram: persist decision, enqueue `cli_approval_resolved`, wake dispatcher. Call `expire_stale_cli_approval_requests` from the task or meeting watchdog loop.

**Acceptance**

- [x] Telegram approve → queued `cli_approval_resolved` + worker wake (or equivalent `runtime_services.enqueue_trigger`).
- [x] Telegram reject → same trigger type with `status=rejected`.
- [x] Desktop approve uses the same helper (wake guaranteed).
- [x] Pytest: approve path creates the trigger without starting Telegram.

**Shipped.** Shared `resume_cli_approval()` in `core/bm_cli/approvals.py` persists the decision and calls `services.enqueue_trigger` (`cli_approval_resolved`). Telegram `/approve` + callback buttons and desktop `POST /api/cli-policy/approvals/{id}/approve|reject` all use it. Task watchdog expires stale approval rows.

---

### HA-CORR-P0-03 — Skip-turn must not exhaust the trigger

| | |
| --- | --- |
| **Severity** | P0 |
| **Area** | correctness / dispatcher |

**Problem.** `_skip_turn` returns `TurnOutcome.skipped` (`trigger_status="skipped"`). Dispatcher treats only `"completed"` as success and only `"failed"` as retryable; `"skipped"` hits `_exhaust_failed_trigger` (permanent fail, may stall the task). `_skip_turn` also sets the task `blocked` when `trigger.task_id` is set.

**Why it matters.** First-run with no model **drops the human chat** and can stall work. Looks like a crash, not a settings gap.

**Approach.** Treat `skipped` as completed-without-retry (or requeue until a model exists). Do not `blocked`/`stalled` the task. Surface the existing “no model configured” activity. Pair with HA-OPS-P1-01 UI banner.

**Acceptance**

- [x] No-model `human_chat` → trigger not `failed`; task not `stalled`/`blocked`.
- [x] Activity/diagnostic still records the skip reason.
- [x] Pytest on dispatcher supervision with a skipped outcome.

**Shipped.** Dispatcher completes a `skipped` trigger instead of routing it through `_exhaust_failed_trigger`. `_skip_turn` no longer sets the bound task `blocked` or tears down the work activity. Diagnostic/activity still record the no-model skip reason.

---

### HA-CORR-P1-06 — Ambiguous task match must not create a duplicate

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness / tasks |

**Problem.** `resolve_existing_task` can return `clarify_ambiguous_match`. `create_or_bind_task` only short-circuits `bind_existing_task`, then always `create_task`.

**Approach.** Return the ambiguous outcome to the API/decision layer; do not insert a third open task. Decision path should `clarify`.

**Acceptance**

- [x] Two open same-title tasks + new bind → no third row.
- [x] API response `outcome=clarify_ambiguous_match` with candidate IDs.
- [x] Accept/defer on an ambiguous title returns `world_feedback` (does not raise / fail the turn).
- [x] Ambiguous delegated children fail the plan; parent is not accepted and the child is not dropped silently.

**Status.** API + `create_or_bind_task` short-circuit are in (`409` + candidates + `bind_task_id`). Decision path matches the delegate handler: accept/defer and `_materialize_work_execution_plan` return `world_feedback` (`expected_action=clarify`). There is no `_require_bound_task` raise and no `continue` on a missing child. Multi-delegation materialization runs in one SQLite transaction: if a later child hits clarify, earlier new child rows roll back (PR #9 non-blocking follow-up).

---

### HA-CORR-P1-07 — `task_assigned` while another activity is live

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness / dispatcher |

**Problem.** `can_dispatch_trigger` returns false for `task_assigned` unless `active_activity is None`. Assignment sits `queued` through meetings/chats/walks.

**Approach.** Allow `task_assigned` to preempt or queue behind a defined set (e.g. allow during conversation; keep blocking only `in_transit`). Document the rule in `policies.py`.

**Acceptance**

- [x] Agent in a conversation still receives `task_assigned` within one dispatcher drain (or a stated max delay).
- [x] In-transit still waits for arrival.

**Shipped.** `can_dispatch_trigger` reads `TriggerPolicy` from `policies.py`. `task_assigned` no longer requires `active_activity is None`; it may claim during conversation / meeting / work / assignment. Movement / `in_transit` still blocks every trigger (including assignment) until arrival. `social` still requires idle + no activity. Tests in `tests/test_dispatch_policy.py`.

---

### HA-SEC-P1-04 — Policy bypass vectors

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | security / bm_cli |
| **Alias** | SEC-P1-04 |

**Problem.** Policy matches the raw command string. `xargs` is `always_allowed`. `never_allowed` misses POSIX shells. Prefix match will not see `xargs rm`.

**Why it matters.** Complements HA-SEC-P0-03. Can be the same PR if the diff stays reviewable; split if seed migration is noisy.

**Approach.** Evaluate `argv[0]` (basename after resolve) against tiers. `xargs` → `never_allowed` or `approval_required`. Document that pipes are not a shell (no `shell=True`) but `xargs` is still an exec multiplexer.

**Acceptance**

- [x] `xargs` is not `always_allowed` in seed. *(done with HA-SEC-P0-03 seed lockdown; also `never_allowed`)*
- [x] `bash -c …` denied by argv[0] even if not in the raw prefix table. *(prefix `bash` is `never_allowed`; `/bin/bash` / `./bash` match via resolved argv[0] basename)*
- [x] Tests for argv[0] vs raw-string mismatch.

**Shipped.** `policy_engine` evaluates the raw command **and** rewrites that replace argv[0] with its path basename (not the symlink target — `/usr/bin/python3` stays `python3`, not `python3.12`), a version-stripped form (`python3.12` → `python3`), and the symlink-target basename when it differs (`/bin/sh` → `dash`). `/bin/bash -c id`, `/usr/bin/python3`, `/bin/xargs rm`, and `./bash` are `never_allowed`. Raw prefix still does not match `/bin/bash` (regression-locked); basename subjects close that hole.

---

### HA-SEC-P1-01 — CLI / tool output role

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | security / LLM |
| **Alias** | SEC-P1-01 |

**Problem.** `loop.py` appends `{"role": "system", "content": result["cli_prompt_content"]}`. File contents and shell stdout inherit system privilege.

**Approach.** Use `role=user` (or a dedicated tool wrapper with hard delimiters). Add a unit test that the continuation builder never emits `role=system` for CLI.

**Acceptance**

- [x] Grep/test: no `cli_prompt_content` attached as `system`.
- [x] One pytest on `_cli_result_to_turn_result` / continuation builder.

**Shipped.** CLI / approval tool output is wrapped with `<<<BOSSMOD_UNTRUSTED_CLI_RESULT>>>` delimiters on `role=user` (`wrap_cli_tool_message` raises if `role=system`). Execution, decision, and approval-resume paths in `loop.py` use `cli_continuation_messages` / `cli_approval_result_messages`. Source lint `lint_source_for_system_role_cli_wrap` scans `core/`, `api/`, and `integrations/`.

---

### HA-PROD-P1-01 — Assign Task in the UI

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | product |

**Problem.** README and First 5 Minutes imply assigning work. No `POST /api/tasks` from `ui/`. Company Tasks and agent Tasks subviews are read-only.

**Approach.** Small form on Company → Tasks (title, assignee, description). Call `POST /api/tasks`. Show reuse vs created (depends on HA-CORR-P0-01). Do not build a full editor.

**Acceptance**

- [x] Operator can assign a task without chatting.
- [x] Assignee gets a `task_assigned` trigger (manual or automated check).
- [x] Empty assignee allowed (unassigned backlog) without crash.

**Shipped.** Company → Tasks has an Assign Task form (title, optional assignee, description) that calls `POST /api/tasks` with the local API token. The UI shows `create_new_task`, `bind_existing_task`, and `clarify_ambiguous_match` honestly, including candidate reuse. Pytest covers create/reuse/clarify/unassigned on the API.

---

### HA-CORR-P1-02 — Task status transition table

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** `update_task` writes any CHECK-legal status. Callers (actions, decision_runtime, watchdog, skip-turn, reset-runtime) do not share a graph.

**Approach.** One function `transition_task(task_id, to, *, reason, actor)` with an allow-map (e.g. `pending→accepted/declined`, `active→waiting/blocked/complete/…`). Reject illegal jumps. Log a `task_events` row.

**Acceptance**

- [x] Illegal jump raises / returns error; DB unchanged.
- [x] Existing happy paths (`pending→accepted→active→complete`) still work.
- [x] Table-driven pytest.

**Shipped.** `transition_task(task_id, to, *, reason, actor)` in `core/tasking/transitions.py` is the shared graph. Identity is allowed; `pending → complete` and other terminal reopen jumps raise `IllegalTaskTransition` and leave the row unchanged. `db.update_task(..., status=...)` uses the same allow-map (no bypass). Status-changing callers (actions, decision_runtime, activity_runtime, watchdog, dispatcher, reset-runtime) go through `transition_task` and write one `task_events` `status_update` row per real jump. Decline/defer/accept no longer append a second `status_update` after `transition_task`. Domain events (`completion`, `blocker`) still sit beside the transition row.

---

### HA-CORR-P1-03 — Watchdog coverage

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** `TaskWatchdog` only lists `status="active"`. Assignments stuck in `accepted`/`waiting` never ping.

**Approach.** Include `accepted` and `waiting` (not `pending` without an assignee). Don’t stall a task that is legitimately waiting on a human clarify if a ping already exists.

**Acceptance**

- [x] Fixture: accepted task, quiet past threshold → `watchdog_status_ping` queued.
- [x] Active task behavior unchanged.

**Shipped.** `TaskWatchdog` scans `active`, `accepted`, and `waiting` (still skips `pending`, including assigned pending). Waiting tasks can be pinged; they are not escalated to `stalled` after an existing ping (human clarify / dependency). Active still pings and still escalates after an ignored ping.

---

### HA-CORR-P1-04 — Telegram `/meeting` vs office meetings

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness / product |

**Problem.** `/meeting` opens a **channel** (`cmd_meeting` → `_open_group_session`). Desktop meetings use `meeting_sessions`, invites, arrival watchdog.

**Approach (pick one, don’t invent a third):**

- **A (KISS):** Rename command to `/group` or `/channel` and document it. Lowest risk.
- **B:** Implement `/meeting` via `create_meeting_session` + existing orchestrator (larger).

Prefer A unless product insists on spatial meetings from Telegram.

**Acceptance**

- [x] UI copy + `/start` help match the implementation.
- [x] No new untested meeting state machine if A is chosen.

**Shipped (A).** Telegram `/group` opens the existing all-agent **channel** (`_open_group_session`). `/meeting` remains a legacy alias for the same handler — it does **not** create a `meeting_session` or hit the room/invite watchdog. `/start` help and Settings → Telegram command copy say so. Spatial office meetings stay desktop-only.

---

### HA-SEC-P1-03 — Unique CLI approval IDs

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | security / Telegram |
| **Alias** | SEC-P1-03 |

**Problem.** `_resolve_approval_by_prefix` returns the first `startswith` match.

**Approach.** Require a unique match; if 0 or >1, refuse. Prefer showing a longer prefix in Telegram messages (8+ hex). Callbacks already use full UUID — keep that.

**Acceptance**

- [x] Two pending IDs sharing a prefix → `/approve yes ab` errors.
- [x] Unique prefix still works.
- [x] Pytest (pure function).

**Shipped.** `resolve_approval_by_unique_prefix` refuses empty, missing, and ambiguous prefixes (no first-match). Telegram `/approve` uses it; callbacks still use the full UUID. Lists show at least 8 hex and extend until unique (`display_approval_prefix`).

---

### HA-OPS-P1-01 — First-run no-model guard

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | ops / product |

**Problem.** Chat HTTP 200 + skip-turn. `_skip_turn` may block a task.

**Approach.** If no model for the mode: do **not** flip task to `blocked` on first skip (settings, not agent failure). Surface a persistent UI banner: “Connect a model in Settings.” Optional: disable Send until a connection exists.

**Acceptance**

- [x] No model + human_chat → activity message, task not `blocked`. *(skip-turn half shipped with HA-CORR-P0-03)*
- [x] Banner or send-disabled in UI when no connections/models.

**Shipped.** Persistent `#no-model-banner` (“Connect a model in Settings”) when `GET /api/connections` is empty. Chat Send + input are disabled until at least one AI connection exists. Banner / send state refresh on app boot, Settings close, and connection create/delete. Skip-turn still does not block the task (HA-CORR-P0-03). Tests in `tests/test_health_ops_ui.py` (source) plus existing `test_run_turn_human_chat_without_model_skips_without_crash`.

---

### HA-OPS-P1-02 — Offline-honest frontend

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | ops |

**Problem.** README “completely offline”; `index.html` + Tauri CSP pull Tailwind/Lucide/Split from CDN.

**Approach.** Vendor the three assets next to `highlight.min.js` **or** change README/CSP copy to “needs network for UI chrome.” Prefer vendoring (matches product claim).

**Acceptance**

- [x] App chrome renders with network disabled **or** README no longer claims fully offline UI.
- [x] Tauri CSP matches the choice.

**Shipped.** Vendored Tailwind Play (`tailwindcss.js`), Lucide 0.469.0, and Split.js 1.6.5 next to `highlight.min.js`. `index.html` no longer hits `cdn.tailwindcss.com` / `unpkg.com`. Tauri CSP is `'self'` plus `'unsafe-inline'` styles and `'unsafe-eval'` (Play compiler). README keeps the offline claim and notes that UI chrome is vendored. Tests in `tests/test_offline_ui.py`.

---

### HA-STRUCT-P1-01 — Split `api/routes.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** 2293 LOC, every HTTP concern.

**Approach.** Move route groups to `api/routes/*.py`, `include_router`. No behavior change. Do this **after** PR #2 so auth middleware stays in `main.py`.

**Acceptance**

- [x] `wc -l` each new module < ~400. *(named routers except `agents.py` — 808; see Shipped)*
- [x] Existing pytest + a manual route list (or `app.routes`) unchanged.
- [x] No logic rewrites in the same PR.

**Shipped.** `api/routes.py` is now the `api/routes/` package. Thin aggregator in `__init__.py` (`from api.routes import router` unchanged). Named routers: `ws`, `agents`, `tasks`, `company_files`, `settings`, `cli_policy`, `runtime`. Shared helpers in `_shared.py`; Desk FS helpers in `_desk.py`. Route table (79 endpoints, path + methods + name) locked in `tests/test_route_split.py`.

Honest size: `ws` 40, `tasks` 204, `runtime` 264, `settings` 278, `cli_policy` 288, `company_files` 399 all meet ~400. **`agents.py` is 808** — desk/chat/meetings/channels stay on that HTTP surface so the PR did not invent an eighth router. `_desk.py` (192) is the extracted helper, not a behavior rewrite.

---

### HA-STRUCT-P1-02 — Split `actions.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** Parse + 12 handlers + task reporting in one file.

**Approach.** Handlers by domain (`work`, `tasks`, `meetings`, `cli`). `execute_action` dispatch table stays. Extract shared follow-up helpers to one module (dedup with `decision_runtime` if cheap).

**Acceptance**

- [x] `actions.py` < ~400 LOC or is dispatch-only. *(428: parse + validate + dispatch; see Shipped)*
- [x] HA-TEST-P1-01 still green.

**Shipped.** `execute_action` / `parse_action` / `TERMINAL_ACTIONS` stay on `core.agent_loop.actions`. Handlers moved mechanically (no behavior rewrite) to:

| Module | Role | ~LOC |
| --- | --- | ---: |
| `actions.py` | parse, validate, dispatch table | 428 |
| `actions_shared.py` | resolve/token/trigger helpers | 121 |
| `task_followups.py` | stakeholder reports + follow-up messages | 239 |
| `actions_cli.py` | `bm_cli` | 43 |
| `actions_work.py` | work / message / walk / idle | 364 |
| `actions_tasks.py` | `taskMessage` / `delegateTask` | 297 |
| `actions_lifecycle.py` | wait / done / block / deleg / drop | 551 |
| `actions_meetings.py` | room + remote meetings | 369 |

Honest size: lifecycle stays one module (complete/blocked/delegated are one family). Decision-runtime follow-up persist is a different contract — not deduped here (would be a behavior-risk rewrite). Import/dispatch smoke in `tests/test_action_split.py`.

---

### HA-STRUCT-P1-03 — Split `loop.py` / `decision_runtime.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** 1723 + 1367 LOC; decision apply vs work-plan vs repair mixed.

**Approach.** Two PRs if needed: (1) move `_run_decision_turn` + repair builders; (2) split `apply_decision` collaborators. No contract changes.

**Acceptance**

- [x] Each file has one sentence responsibility in the module docstring.
- [x] No new public behavior.

**Shipped.** `run_turn` stays on `core.agent_loop.loop`; `apply_decision` / `summarize_decision` stay on `core.agent_loop.decision_runtime`. `_cli_result_to_turn_result` is still importable from `loop` (re-export). Mechanical peel — no contract or feature rewrite.

| Module | Role | ~LOC |
| --- | --- | ---: |
| `loop.py` | public `run_turn` router (mode/model/context + dispatch) | 178 |
| `decision_turn.py` | decision LLM loop + CLI lookup + apply | 467 |
| `execution_turn.py` | execution action loop + CLI-approval resume | 659 |
| `turn_context.py` | trigger classification + prompt snapshot fields | 132 |
| `turn_helpers.py` | repair/continuation, traces, CLI result, skip/finalize | 409 |
| `decision_runtime.py` | `apply_decision` / `summarize_decision` dispatch | 386 |
| `decision_work_plan.py` | resolve + materialize delegated work plans | 239 |
| `decision_task_bind.py` | create/bind/defer work tasks + contracts | 238 |
| `decision_replies.py` | persist replies + shared meeting/channel queues | 500 |
| `decision_resume.py` | resume waiting work + close assignment wrappers | 154 |

Honest leftover size: `execution_turn.py` stays large because the multi-step action loop is one control flow (extracting it further would be a rewrite). `decision_replies.py` is one family (persist reply + task follow-up + shared-queue advance). Import/dispatch smoke in `tests/test_loop_split.py`. HA-STRUCT-P1-05 (managed_writer / context_builder preview) shipped later.

---

### HA-STRUCT-P1-04 — Split settings JS

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** `settings-view.js` 1921; `cli-policy-section.js` 1323. IIFEs already exist.

**Approach.** One file per section; `index.html` script tags. Extract simulator.

**Acceptance**

- [x] Settings sections still open; simulator still executes (or dry-runs if HA-SEC-P1-06 landed).
- [x] After PR #2, all fetches go through the token helper.

**Shipped.** Mechanical peel of the existing settings IIFEs into sibling files. `settings-view.js` is the shell (nav + `switchSection`). Simulator tab moved to `cli-policy-simulator.js` (`CliPolicySimulator.render`); Enter is still dry-run and **Execute for real** still sends `execute=true` (HA-SEC-P1-06). Fetches stay on `window.fetch`, which `api-auth.js` already patches with `X-BossMod-Token`. Script order locked in `tests/test_settings_js_split.py`.

| Module | Role | ~LOC |
| --- | --- | ---: |
| `settings-view.js` | nav shell + section dispatch | 122 |
| `settings-shared.js` | `initResizeHandle` | 34 |
| `settings-connections.js` | AI Connections | 330 |
| `settings-personalities.js` | AI Personalities | 176 |
| `settings-system.js` | System Settings | 261 |
| `settings-prompt-template.js` | System Prompt Template | 158 |
| `settings-advanced.js` | Advanced System Settings | 285 |
| `settings-runtime-contracts.js` | Runtime Contracts | 333 |
| `settings-telegram.js` | Telegram | 262 |
| `cli-policy-simulator.js` | Simulator tab | 414 |
| `cli-policy-section.js` | CLI Policy shell + other tabs | 961 |

Honest leftover: `cli-policy-section.js` stays large because Rules / Virtual Commands / Settings / Approvals share `rulesCache` / `agentsCache` in one IIFE. Splitting those tabs would be a state-module rewrite, not this peel. HA-STRUCT-P1-08 (shared JS API client) is **not** this PR — raw `fetch('/api/...')` remains; the token helper still wraps `window.fetch`.

---

### HA-STRUCT-P1-05 — Split managed writer + context preview

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** `managed_writer.py` 1851; `context_builder.py` 1229 mixes live and Settings preview.

**Approach.** Package split only. Preview functions → `context_preview.py`.

**Acceptance**

- [x] Settings contract preview still renders.
- [x] Managed write / batch / section entrypoints unchanged.

**Shipped.** Package peel only — no writer/preview behavior rewrite. `from core.bm_cli.managed_writer import …` and `context_builder.preview_*` still resolve. Settings preview lives in `core/llm/context_preview.py`; `context_builder` re-exports via `__getattr__`. Tests in `tests/test_managed_writer_split.py` and `tests/test_context_preview.py`.

| Module | Role | ~LOC |
| --- | --- | ---: |
| `managed_writer/__init__.py` | public re-exports | 27 |
| `managed_writer/types.py` | dataclasses + sentinels | 123 |
| `managed_writer/detect.py` | `is_managed_*_request` | 35 |
| `managed_writer/helpers.py` | progress / annotate / text | 226 |
| `managed_writer/prompts.py` | instruction builders | 139 |
| `managed_writer/write.py` | `run_managed_write` | 98 |
| `managed_writer/batch.py` | `run_managed_batch_write` + manifest | 447 |
| `managed_writer/section.py` | `run_managed_section_rewrite` | 408 |
| `managed_writer/generate.py` | direct + sectioned generation | 515 |
| `context_builder.py` | live turn assembly | 977 |
| `context_preview.py` | Settings contract/prompt preview | 282 |

Honest leftover: `generate.py` / `batch.py` / `section.py` stay large because each is one authoring control flow. `context_builder.py` is still the live assembler (formatters + template context). Splitting those further would be a rewrite. HA-STRUCT-P1-08 (shared JS API client) is **not** this PR.

---

### HA-STRUCT-P1-06 — Break core → API import

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DI |

**Problem.** `core/runtime/services.py` imports `api.websocket.manager`.

**Approach.** Broadcast through `runtime_events` (already used in the worker) or a small `EventSink` protocol set in `main.py` lifespan. `RuntimeServices` should not know FastAPI.

**Acceptance**

- [x] `grep` in `core/` has no `from api.`.
- [x] World/chat/diagnostic WS events still arrive.

**Shipped (same PR as HA-STRUCT-P1-01).** `RuntimeServices` no longer imports `api.websocket.manager`. `main.py` lifespan calls `set_event_sink(manager)` before `start()`. Worker events dispatch through that `EventSink` protocol. Tests in `tests/test_runtime_event_sink.py` cover the core-import lint, injected-sink dispatch, and no-sink no-raise. Live WS still uses the same `ConnectionManager` methods; route handlers still import `manager` directly.

---

### HA-STRUCT-P1-07 — Dedup meeting/channel rounds

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DRY |

**Problem.** Parallel DB + loop modules differ by foreign key name.

**Approach.** Generic `response_rounds` table helpers **or** a thin wrapper that binds table names. One behavior test for each remaining façade.

**Acceptance**

- [x] Single implementation of reserve / observe / complete.
- [x] Existing meeting kickoff test still passes; add one channel equivalent.

**Shipped.** One SQL state machine in `db/response_rounds.py` (schema binds table + parent FK). One loop coordinator in `core/agent_loop/response_rounds.py`. Meeting/channel modules stay thin façades so existing `db.*` / import names do not change. `db/__init__.py` was not grown (HA-STRUCT-P2-01). Tests: `tests/test_response_rounds.py` (meeting observe + reserve/complete for both façades); existing `tests/test_meeting_orchestrator.py` kickoff and `tests/test_channel_rounds.py` observe still apply.

Honest leftover: two physical tables remain (`meeting_response_*` vs `channel_response_*`). Unifying them would be a migration, not this DRY peel.

---

### HA-SEC-P1-05 — Secrets at rest

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | security |
| **Alias** | SEC-P1-05 |

**Problem.** `settings`, `ai_connections.api_key`, `agents.api_key` are plaintext SQLite.

**Approach.** This is **not** a weekend encrypt-everything. PR-sized: design note + encrypt the three columns with a key from OS keychain or a file `chmod 600` under the project data dir. Or document “disk encryption is the control” if that is the product choice.

**Acceptance**

- [x] Written decision in `docs/` (or this file updated).
- [x] If encrypting: keys never written plaintext on `GET` (PR #2) **and** not plaintext in a new DB dump of those columns.

**Shipped (scoped).** Decision: [`docs/SECRETS_AT_REST.md`](SECRETS_AT_REST.md). File key `{db_dir}/.bossmod_data_key` (`chmod 600`), not OS keychain. Wraps `ai_connections.api_key`, `agents.api_key`, and secret settings (`telegram_bot_token`, `local_api_token`) as `bm1:` blobs. CRUD decrypts for the app; `GET` redaction is unchanged. Existing plaintext rows are rewritten on `init_db`. Disk encryption remains the control against theft of the whole data dir. Tests in `tests/test_secrets_at_rest.py`.

---

### HA-SEC-P1-02 — Trigger leases

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |
| **Alias** | SEC-P1-02 |

**Problem.** Crash mid-turn requeues the same trigger (`requeue_stale_triggers` on `dispatcher.start`). No `claimed_at` heartbeat. Live double-run on a healthy worker was **overstated**.

**Approach.** Store `claim_generation` / lease token; heartbeat `claimed_at` during long LLM/shell. Only requeue if worker heartbeat is stale (already have `runtime_worker_state`).

**Acceptance**

- [x] Worker restart after a completed trigger does not replay it.
- [x] Worker kill mid-claim eventually requeues once.
- [x] Test with a fake long turn (monkeypatch).

**Shipped.** `claim_trigger` issues `claim_generation` + `claim_lease` and heartbeats `claimed_at` during `_run_trigger`. `complete_agent_trigger` / `fail_agent_trigger` / `retry_agent_trigger` refuse a stale `claim_generation` (claimed + matching generation only). Dispatcher fail/retry/exhaust call sites pass the in-flight generation and no-op if the lease was reclaimed. `requeue_stale_triggers(force=True)` on dispatcher start recovers orphaned `claimed` rows only (completed stays completed). A live worker (`runtime_worker_state` running + fresh heartbeat) is not stolen by a timeout-only requeue. Unguarded complete/fail/retry (no generation) remain helper hatches on `queued`/`claimed` only — they do not flip a completed row. `claim_lease` is still written/cleared, not checked (generation is the guard). Tests in `tests/test_trigger_leases.py`.

---

### HA-LOOP-P1-07 — Seed meeting watchdog settings

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | ops |
| **Alias** | LOOP-P1-07 |

**Problem.** `meeting_watchdog_check_interval_seconds`, `meeting_invite_accept_timeout_seconds`, `meeting_invite_arrival_timeout_seconds` used with `or` defaults; absent from `_SEED_SETTINGS`.

**Approach.** Seed + Settings UI rows. Remove hidden fallbacks or keep them equal to seed.

**Acceptance**

- [x] Fresh DB contains the three keys.
- [x] Settings page can change them; watchdog reads `config.get_*`.

**Shipped.** Seeded under `simulation`: `meeting_watchdog_check_interval_seconds=5`, `meeting_invite_accept_timeout_seconds=90`, `meeting_invite_arrival_timeout_seconds=180`. Settings → Simulation exposes the three rows. `read_meeting_watchdog_settings()` uses `config.get_*` with fallbacks equal to those seeds. Existing DBs pick up missing keys on `init_db` via `seed_defaults()` (no overwrite).

---

### HA-SEC-P1-06 — Simulator dry-run default

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | security |
| **Alias** | SEC-P1-06 |

**Problem.** `POST /api/cli-policy/simulator/execute` runs `execute_bm_cli` for real. PR #2 only adds auth.

**Approach.** Default `dry_run=true` (policy + parse only). Require an explicit `execute=true` for writes/shell. Separate button in `cli-policy-section.js`.

**Acceptance**

- [x] Default POST does not create files.
- [x] Explicit execute still works for operators.

**Shipped.** `POST /api/cli-policy/simulator/execute` defaults to `dry_run=true` (`preview_bm_cli`: parse + policy only). Real writes/shell require `execute=true` or `dry_run=false`. Settings → CLI Policy simulator Enter is dry-run; a separate **Execute for real** button sends `execute=true`. Tests in `tests/test_cli_simulator.py`.

---

### HA-CORR-P1-05 — Reset-runtime and skip-turn hygiene

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** Reset blocks only `open_activities[0]`’s task. Skip-turn blocks by `trigger.task_id` (settings gap).

**Approach.** Reset: block or pause **all** open work activities’ tasks. Skip-turn: see HA-OPS-P1-01 (don’t block).

**Acceptance**

- [x] Two open work activities → both tasks `blocked` or both `waiting` with a note.
- [x] Pytest with two activities.

**Shipped.** `POST /api/agents/{id}/reset-runtime` blocks **every** distinct open work-activity task (`pending`/`accepted`/`active`/`waiting` → `blocked` with an operator note), not just `open_activities[0]`. Response keeps `blocked_task_id` and adds `blocked_task_ids`. Skip-turn already does not block (HA-CORR-P0-03); this PR adds a two-activity skip regression so neither task flips to `blocked`.

---

### HA-TEST-P1-02 — Policy / path-jail tests

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | tests |

Companion to HA-SEC-P0-03 / HA-SEC-P1-04 if those PRs shipped without tests (they should not). Standalone if needed.

**Acceptance.** Interpreters not always-allowed; absolute path denied; unique approval prefix (if HA-SEC-P1-03 landed).

- [x] Interpreters not always-allowed; absolute path denied.
- [x] Unique approval prefix (HA-SEC-P1-03).

**Shipped (already on main).** Path-jail + seed/policy tests landed with HA-SEC-P0-03 / HA-SEC-P1-04 in `tests/test_cli_policy.py`. Unique-approval-prefix coverage landed with HA-SEC-P1-03 in `tests/test_cli_approval_prefix.py`. No additional standalone PR.

---

### HA-TEST-P1-03 — Task / meeting / channel slice

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | tests |

Add: bind vs create; kickoff (exists); one channel round observe; watchdog ping enqueue. No LLM.

**Acceptance**

- [x] Bind vs create (and ambiguous match does not insert a third row).
- [x] Meeting kickoff (existing `tests/test_meeting_orchestrator.py`).
- [x] One channel round observe.
- [x] Watchdog ping enqueue.
- [x] `pytest tests/test_tasking.py tests/test_meeting_orchestrator.py tests/test_channel_rounds.py` green.

**Shipped.** `tests/test_tasking.py` covers create/bind/clarify + watchdog ping enqueue. `tests/test_channel_rounds.py` observes one shared-channel candidate and completes the round. Meeting kickoff was already in `tests/test_meeting_orchestrator.py`.

---

### HA-STRUCT-P1-08 — Shared JS API client

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DRY |

**Problem.** Dozens of raw `fetch('/api/...')`. After PR #2, any missed call 401s.

**Approach.** Finish `api-auth.js` (or `api-client.js`) and migrate remaining files. One PR, mechanical.

**Acceptance**

- [x] `rg "fetch\\('/api" ui/static/js` only hits the helper (plus vendor).
- [x] Pause, chat, files, settings, simulator still work.

**Shipped.** New `ui/static/js/api-client.js` exports `apiFetch()` / `window.BossModApi.fetch` (same signature as `fetch`). Every previous `ui/static/js` `/api` call site now uses it. `api-auth.js` still patches `window.fetch` and WebSocket `?token=` — `apiFetch` delegates to that wrap and also sets `X-BossMod-Token` itself. Script order: `api-auth.js` → `api-client.js` → the rest. Tests in `tests/test_js_api_client.py` (source lint + Node harness).

Honest leftover: company-file-ops still has local `apiPost` / `apiPatch` / `apiDelete` wrappers; they now call `apiFetch`. No JSON convenience layer — call sites still parse `res.json()` themselves.

PR #19 follow-up (this PR): company image preview no longer uses bare `<img src="/api/company/files/raw">`. `apiFetchBlobUrl()` loads the bytes with `X-BossMod-Token` and sets a blob object URL (revoked on close).

---

### HA-SEC-NEW-01 — Connection test URL allowlist

| | |
| --- | --- |
| **Severity** | P2 (P1 on unauthenticated `main`) |
| **Area** | security |

**Problem.** `POST /api/connections/test` fetches caller URL + `/models`.

**Approach.** After PR #2: require https or loopback; block link-local / metadata IPs. Optional.

**Acceptance.** `http://127.0.0.1:9` and a documented-bad IP rejected.

**Shipped.** `validate_connection_test_url()` in `core/llm/connection_url.py` allows https (non-metadata) and http/https loopback (`127.0.0.1`, `::1`, `localhost`) for local Ollama / LM Studio. Blocks non-loopback http, link-local (`169.254.0.0/16`), AWS/GCP/Alibaba metadata hosts, and userinfo in the URL. `POST /api/connections/test` returns `{ok: false}` before fetch on allowlist failure.

Honest leftover: `http://127.0.0.1:9` is **allowed by the allowlist** (loopback) and then fails closed on connect (`ok: false`) — rejecting loopback http would break local models. Documented-bad IP `http://169.254.169.254/` is rejected before fetch. Tests in `tests/test_connection_url.py`.

---

### HA-OPS-P2-01 — Unused dependencies

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | ops |

Remove `duckdb` from `pyproject.toml` (runtime is SQLite). Remove unused `twilio` extra or implement it. Keep SQL `$1` rewriter — it is SQLite compat, not DuckDB.

**Acceptance.** `rg duckdb` / `rg twilio` only in lockfile history/docs; `uv lock` updated.

**Shipped.** Removed `duckdb` from main dependencies and dropped the unused `notifications` extra (`twilio` + duplicate `python-telegram-bot`). Telegram stays a main dependency. SQL `$1` / `ILIKE` rewriter in `db/connection.py` is unchanged (SQLite compat, not DuckDB). Docs still mention the old unused deps as historical audit notes.

---

### HA-OPS-P2-02 — Desktop process cleanup

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | ops |
| **Alias** | PR #1 P2 note |

Replace `pkill -f <main.py path>` with the Child PID Tauri already stores.

**Acceptance.** Second launch does not kill unrelated `python …/main.py` processes (document how you tested).

**Shipped.** `desktop/src/main.rs` writes `.bossmod-backend.pid` after spawn. Relaunch reads that PID and `kill`s it only if `/proc/{pid}/cmdline` still contains this repo’s `main.py`. Window close removes the pid file after killing the recorded child. `pkill -f` is gone. `.bossmod-backend.pid` is gitignored.

How this was tested: source contract in `tests/test_health_ops_ui.py` (`pkill` absent; pid-file + cmdline check present). A live second Tauri launch was **not** run in this environment (no desktop session).

---

### HA-OPS-P2-03 — README accuracy

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | product |

Stack table still says SQLITE (correct) while `pyproject.toml` unused-depends on DuckDB — mention SQLite only, or drop the dep (HA-OPS-P2-01). After PR #2, document `X-BossMod-Token`. UI chrome CDN claim is closed by HA-OPS-P1-02 (vendored). Personality count (9 seeded) is already accurate.

**Acceptance.** README stack/auth/offline sentences match the tree; no false “10 personalities” nit.

**Shipped.** README stack table is SQLite (no DuckDB). Local API token (`X-BossMod-Token`) and vendored offline UI chrome were already documented after PR #2 / HA-OPS-P1-02. Personality count remains 9. ARCHITECTURE desktop/UI rows updated (PID file, vendored Tailwind).

---

### HA-CORR-P2-01 — Safe config ints

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | correctness |

`config.get_int` / `get_float` should return `None` (and log) on `ValueError`, not crash the watchdog.

**Acceptance.** Setting `tick_interval=nope` does not take down the worker.

**Shipped.** `config.get_int` / `get_float` catch `ValueError`, log a warning, and return `None`. Call sites that already use `or <default>` keep running. `require_int` / `require_float` raise `ConfigError` instead of a bare `ValueError`. Tests in `tests/test_config_ints.py`.

---

### HA-STRUCT-P2-01 — `db` barrel diet

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | DI |

Stop adding to `db/__init__.py`. New modules import `db.tasks`. Optional later: split `__all__` by domain. Not a rewrite.

**Acceptance.** Review checklist in CONTRIBUTING or this file; one new endpoint does not add 10 re-exports “just in case.”

**Shipped (checklist only — no barrel rewrite).** There is no CONTRIBUTING file; the review rule lives here:

1. New persistence helpers go in a domain module (`db/tasks.py`, `db/settings.py`, …).
2. Callers that already `import db` may keep using the barrel **only** if the symbol is already exported.
3. New endpoints / new modules should `from db.tasks import …` (or the matching domain module) instead of adding re-exports “just in case.”
4. Do not grow `db/__init__.py` unless an existing `import db` call site would otherwise break, and then add the minimum symbol.
5. Never import `api` from `db/`.

This PR does not add re-exports to `db/__init__.py`.

---

### HA-PROD-P2-01 — Chat no-reply UX

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | product |

Walk/idle after chat leaves an empty thread. Show a system receipt (“Alex is walking to their desk”) using existing activity/WS events.

**Acceptance.** After a walk_to turn, chat shows a receipt without enabling “show system notifications” if that is the default operator path (product call).

**Shipped.** Walk / meeting receipts (`notification_kind=receipt`) stay visible in the chat thread even when “Show system notifications” is off. The toggle still hides other system rows (completion / blocked / handoff). Backend already persisted and broadcast these receipts on `human_chat` + `walkTo`. Tests: `tests/test_chat_receipts.py` + source lint in `tests/test_health_ops_ui.py`.

---

## Out of scope (explicit)

- Multi-tenant hosted hardening.
- Full rewrite of the agent loop.
- Replacing SQLite.
- Inventing a plugin system (`plugins/` is empty — leave it or delete the dir in a P2).
- Implementing Twilio.
- Prompt-quality / personality tone work.
- Bubblewrap/landlock (follow-up after HA-SEC-P0-03 if still needed).
