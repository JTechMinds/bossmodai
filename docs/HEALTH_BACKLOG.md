# BossMod AI — Health backlog

Ordered work list from [`HEALTH_AUDIT.md`](HEALTH_AUDIT.md). Each item is meant to be **one PR**. Do not combine a security P0 with a JS split.

**Legend:** P0 = correctness / security / data-loss. P1 = high-leverage health. P2 = cleanup.  
**In-flight (do not redo):** PR #1 (this audit’s security ancestor), PR #2 (SEC-P0-01 / SEC-P0-02).

**ID prefix:** `HA-` (health audit) so these do not collide with `SEC-P0-*` in `docs/AUDIT_P0_P1.md`. Where an item continues that audit, the old ID is listed under **Alias**.

---

## Sequence at a glance

| Order | ID | Title | Sev | Area |
| ---: | --- | --- | --- | --- |
| — | *(PR #2)* | Fail-closed Telegram + local API token + redaction | P0 | security |
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

## In-flight (not in this backlog)

### PR #2 — SEC-P0-01 / SEC-P0-02

- **Problem:** Telegram fail-open; unauthenticated REST/WS return secrets and expose reseed/simulator.
- **Status:** Open branch `cursor/sec-p0-01-p0-02-b82e`. Review and merge before starting HA-STRUCT-P1-08 (JS must send `X-BossMod-Token`).
- **Acceptance:** Empty allowlist denies; settings/connections redact; unauthenticated `POST /api/settings/reseed` → 401. Already claimed by that PR’s tests.

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

- [ ] `cat /etc/passwd` denied by path jail even if policy would allow `cat`.
- [ ] Seed rules: no interpreter/`xargs` in `always_allowed`; shells in `never_allowed`.
- [ ] Existing seed users: migration or “seed defaults” button updates dangerous rows (document the choice).
- [ ] Tests in `tests/test_cli_policy.py` (can land with HA-TEST-P1-02 if this PR is too fat — prefer tests here).

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

- [ ] `uv run pytest -q` on a clean checkout is green.
- [ ] Smoke script either runs those tests or is removed.
- [ ] Tests use `BOSSMOD_DB_PATH` temp DB (conftest already does).

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

- [ ] Re-POST same title/assignee → trigger row queued or already-open trigger updated.
- [ ] Response body includes `outcome` (`create_new_task` \| `bind_existing_task` \| `clarify_ambiguous_match`).
- [ ] Pytest in the module from HA-TEST-P1-01.

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

- [ ] Telegram approve → queued `cli_approval_resolved` + worker wake (or equivalent `runtime_services.enqueue_trigger`).
- [ ] Telegram reject → same trigger type with `status=rejected`.
- [ ] Desktop approve uses the same helper (wake guaranteed).
- [ ] Pytest: approve path creates the trigger without starting Telegram.

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

- [ ] No-model `human_chat` → trigger not `failed`; task not `stalled`/`blocked`.
- [ ] Activity/diagnostic still records the skip reason.
- [ ] Pytest on dispatcher supervision with a skipped outcome.

---

### HA-CORR-P1-06 — Ambiguous task match must not create a duplicate

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness / tasks |

**Problem.** `resolve_existing_task` can return `clarify_ambiguous_match`. `create_or_bind_task` only short-circuits `bind_existing_task`, then always `create_task`.

**Approach.** Return the ambiguous outcome to the API/decision layer; do not insert a third open task. Decision path should `clarify`.

**Acceptance**

- [ ] Two open same-title tasks + new bind → no third row.
- [ ] API response `outcome=clarify_ambiguous_match` with candidate IDs.

---

### HA-CORR-P1-07 — `task_assigned` while another activity is live

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness / dispatcher |

**Problem.** `can_dispatch_trigger` returns false for `task_assigned` unless `active_activity is None`. Assignment sits `queued` through meetings/chats/walks.

**Approach.** Allow `task_assigned` to preempt or queue behind a defined set (e.g. allow during conversation; keep blocking only `in_transit`). Document the rule in `policies.py`.

**Acceptance**

- [ ] Agent in a conversation still receives `task_assigned` within one dispatcher drain (or a stated max delay).
- [ ] In-transit still waits for arrival.

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

- [ ] `xargs` is not `always_allowed` in seed.
- [ ] `bash -c …` denied by argv[0] even if not in the raw prefix table.
- [ ] Tests for argv[0] vs raw-string mismatch.

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

- [ ] Grep/test: no `cli_prompt_content` attached as `system`.
- [ ] One pytest on `_cli_result_to_turn_result` / continuation builder.

---

### HA-PROD-P1-01 — Assign Task in the UI

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | product |

**Problem.** README and First 5 Minutes imply assigning work. No `POST /api/tasks` from `ui/`. Company Tasks and agent Tasks subviews are read-only.

**Approach.** Small form on Company → Tasks (title, assignee, description). Call `POST /api/tasks`. Show reuse vs created (depends on HA-CORR-P0-01). Do not build a full editor.

**Acceptance**

- [ ] Operator can assign a task without chatting.
- [ ] Assignee gets a `task_assigned` trigger (manual or automated check).
- [ ] Empty assignee allowed (unassigned backlog) without crash.

---

### HA-CORR-P1-02 — Task status transition table

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** `update_task` writes any CHECK-legal status. Callers (actions, decision_runtime, watchdog, skip-turn, reset-runtime) do not share a graph.

**Approach.** One function `transition_task(task_id, to, *, reason, actor)` with an allow-map (e.g. `pending→accepted/declined`, `active→waiting/blocked/complete/…`). Reject illegal jumps. Log a `task_events` row.

**Acceptance**

- [ ] Illegal jump raises / returns error; DB unchanged.
- [ ] Existing happy paths (`pending→accepted→active→complete`) still work.
- [ ] Table-driven pytest.

---

### HA-CORR-P1-03 — Watchdog coverage

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** `TaskWatchdog` only lists `status="active"`. Assignments stuck in `accepted`/`waiting` never ping.

**Approach.** Include `accepted` and `waiting` (not `pending` without an assignee). Don’t stall a task that is legitimately waiting on a human clarify if a ping already exists.

**Acceptance**

- [ ] Fixture: accepted task, quiet past threshold → `watchdog_status_ping` queued.
- [ ] Active task behavior unchanged.

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

- [ ] UI copy + `/start` help match the implementation.
- [ ] No new untested meeting state machine if A is chosen.

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

- [ ] Two pending IDs sharing a prefix → `/approve yes ab` errors.
- [ ] Unique prefix still works.
- [ ] Pytest (pure function).

---

### HA-OPS-P1-01 — First-run no-model guard

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | ops / product |

**Problem.** Chat HTTP 200 + skip-turn. `_skip_turn` may block a task.

**Approach.** If no model for the mode: do **not** flip task to `blocked` on first skip (settings, not agent failure). Surface a persistent UI banner: “Connect a model in Settings.” Optional: disable Send until a connection exists.

**Acceptance**

- [ ] No model + human_chat → activity message, task not `blocked`.
- [ ] Banner or send-disabled in UI when no connections/models.

---

### HA-OPS-P1-02 — Offline-honest frontend

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | ops |

**Problem.** README “completely offline”; `index.html` + Tauri CSP pull Tailwind/Lucide/Split from CDN.

**Approach.** Vendor the three assets next to `highlight.min.js` **or** change README/CSP copy to “needs network for UI chrome.” Prefer vendoring (matches product claim).

**Acceptance**

- [ ] App chrome renders with network disabled **or** README no longer claims fully offline UI.
- [ ] Tauri CSP matches the choice.

---

### HA-STRUCT-P1-01 — Split `api/routes.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** 2293 LOC, every HTTP concern.

**Approach.** Move route groups to `api/routes/*.py`, `include_router`. No behavior change. Do this **after** PR #2 so auth middleware stays in `main.py`.

**Acceptance**

- [ ] `wc -l` each new module < ~400.
- [ ] Existing pytest + a manual route list (or `app.routes`) unchanged.
- [ ] No logic rewrites in the same PR.

---

### HA-STRUCT-P1-02 — Split `actions.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** Parse + 12 handlers + task reporting in one file.

**Approach.** Handlers by domain (`work`, `tasks`, `meetings`, `cli`). `execute_action` dispatch table stays. Extract shared follow-up helpers to one module (dedup with `decision_runtime` if cheap).

**Acceptance**

- [ ] `actions.py` < ~400 LOC or is dispatch-only.
- [ ] HA-TEST-P1-01 still green.

---

### HA-STRUCT-P1-03 — Split `loop.py` / `decision_runtime.py`

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** 1723 + 1367 LOC; decision apply vs work-plan vs repair mixed.

**Approach.** Two PRs if needed: (1) move `_run_decision_turn` + repair builders; (2) split `apply_decision` collaborators. No contract changes.

**Acceptance**

- [ ] Each file has one sentence responsibility in the module docstring.
- [ ] No new public behavior.

---

### HA-STRUCT-P1-04 — Split settings JS

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** `settings-view.js` 1921; `cli-policy-section.js` 1323. IIFEs already exist.

**Approach.** One file per section; `index.html` script tags. Extract simulator.

**Acceptance**

- [ ] Settings sections still open; simulator still executes (or dry-runs if HA-SEC-P1-06 landed).
- [ ] After PR #2, all fetches go through the token helper.

---

### HA-STRUCT-P1-05 — Split managed writer + context preview

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | structure |

**Problem.** `managed_writer.py` 1851; `context_builder.py` 1229 mixes live and Settings preview.

**Approach.** Package split only. Preview functions → `context_preview.py`.

**Acceptance**

- [ ] Settings contract preview still renders.
- [ ] Managed write / batch / section entrypoints unchanged.

---

### HA-STRUCT-P1-06 — Break core → API import

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DI |

**Problem.** `core/runtime/services.py` imports `api.websocket.manager`.

**Approach.** Broadcast through `runtime_events` (already used in the worker) or a small `EventSink` protocol set in `main.py` lifespan. `RuntimeServices` should not know FastAPI.

**Acceptance**

- [ ] `grep` in `core/` has no `from api.`.
- [ ] World/chat/diagnostic WS events still arrive.

---

### HA-STRUCT-P1-07 — Dedup meeting/channel rounds

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DRY |

**Problem.** Parallel DB + loop modules differ by foreign key name.

**Approach.** Generic `response_rounds` table helpers **or** a thin wrapper that binds table names. One behavior test for each remaining façade.

**Acceptance**

- [ ] Single implementation of reserve / observe / complete.
- [ ] Existing meeting kickoff test still passes; add one channel equivalent.

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

- [ ] Written decision in `docs/` (or this file updated).
- [ ] If encrypting: keys never written plaintext on `GET` (PR #2) **and** not plaintext in a new DB dump of those columns.

Label hunches: keychain UX on Linux is messy; file-based key may be enough for desktop.

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

- [ ] Worker restart after a completed trigger does not replay it.
- [ ] Worker kill mid-claim eventually requeues once.
- [ ] Test with a fake long turn (monkeypatch).

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

- [ ] Fresh DB contains the three keys.
- [ ] Settings page can change them; watchdog reads `config.get_*`.

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

- [ ] Default POST does not create files.
- [ ] Explicit execute still works for operators.

---

### HA-CORR-P1-05 — Reset-runtime and skip-turn hygiene

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | correctness |

**Problem.** Reset blocks only `open_activities[0]`’s task. Skip-turn blocks by `trigger.task_id` (settings gap).

**Approach.** Reset: block or pause **all** open work activities’ tasks. Skip-turn: see HA-OPS-P1-01 (don’t block).

**Acceptance**

- [ ] Two open work activities → both tasks `blocked` or both `waiting` with a note.
- [ ] Pytest with two activities.

---

### HA-TEST-P1-02 — Policy / path-jail tests

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | tests |

Companion to HA-SEC-P0-03 / HA-SEC-P1-04 if those PRs shipped without tests (they should not). Standalone if needed.

**Acceptance.** Interpreters not always-allowed; absolute path denied; unique approval prefix (if HA-SEC-P1-03 landed).

---

### HA-TEST-P1-03 — Task / meeting / channel slice

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | tests |

Add: bind vs create; kickoff (exists); one channel round observe; watchdog ping enqueue. No LLM.

**Acceptance.** `pytest tests/test_tasking.py tests/test_meeting_orchestrator.py tests/test_channel_rounds.py` green.

---

### HA-STRUCT-P1-08 — Shared JS API client

| | |
| --- | --- |
| **Severity** | P1 |
| **Area** | DRY |

**Problem.** Dozens of raw `fetch('/api/...')`. After PR #2, any missed call 401s.

**Approach.** Finish `api-auth.js` (or `api-client.js`) and migrate remaining files. One PR, mechanical.

**Acceptance**

- [ ] `rg "fetch\\('/api" ui/static/js` only hits the helper (plus vendor).
- [ ] Pause, chat, files, settings, simulator still work.

---

### HA-SEC-NEW-01 — Connection test URL allowlist

| | |
| --- | --- |
| **Severity** | P2 (P1 on unauthenticated `main`) |
| **Area** | security |

**Problem.** `POST /api/connections/test` fetches caller URL + `/models`.

**Approach.** After PR #2: require https or loopback; block link-local / metadata IPs. Optional.

**Acceptance.** `http://127.0.0.1:9` and a documented-bad IP rejected.

---

### HA-OPS-P2-01 — Unused dependencies

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | ops |

Remove `duckdb` from `pyproject.toml` (runtime is SQLite). Remove unused `twilio` extra or implement it. Keep SQL `$1` rewriter — it is SQLite compat, not DuckDB.

**Acceptance.** `rg duckdb` / `rg twilio` only in lockfile history/docs; `uv lock` updated.

---

### HA-OPS-P2-02 — Desktop process cleanup

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | ops |
| **Alias** | PR #1 P2 note |

Replace `pkill -f <main.py path>` with the Child PID Tauri already stores.

**Acceptance.** Second launch does not kill unrelated `python …/main.py` processes (document how you tested).

---

### HA-OPS-P2-03 — README accuracy

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | product |

Stack table still says SQLITE (correct) while `pyproject.toml` unused-depends on DuckDB — mention SQLite only, or drop the dep (HA-OPS-P2-01). After PR #2, document `X-BossMod-Token`. Don’t claim a fully offline UI until HA-OPS-P1-02. Personality count (9 seeded) is already accurate.

**Acceptance.** README stack/auth/offline sentences match the tree; no false “10 personalities” nit.

---

### HA-CORR-P2-01 — Safe config ints

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | correctness |

`config.get_int` / `get_float` should return `None` (and log) on `ValueError`, not crash the watchdog.

**Acceptance.** Setting `tick_interval=nope` does not take down the worker.

---

### HA-STRUCT-P2-01 — `db` barrel diet

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | DI |

Stop adding to `db/__init__.py`. New modules import `db.tasks`. Optional later: split `__all__` by domain. Not a rewrite.

**Acceptance.** Review checklist in CONTRIBUTING or this file; one new endpoint does not add 10 re-exports “just in case.”

---

### HA-PROD-P2-01 — Chat no-reply UX

| | |
| --- | --- |
| **Severity** | P2 |
| **Area** | product |

Walk/idle after chat leaves an empty thread. Show a system receipt (“Alex is walking to their desk”) using existing activity/WS events.

**Acceptance.** After a walk_to turn, chat shows a receipt without enabling “show system notifications” if that is the default operator path (product call).

---

## Out of scope (explicit)

- Multi-tenant hosted hardening.
- Full rewrite of the agent loop.
- Replacing SQLite.
- Inventing a plugin system (`plugins/` is empty — leave it or delete the dir in a P2).
- Implementing Twilio.
- Prompt-quality / personality tone work.
- Bubblewrap/landlock (follow-up after HA-SEC-P0-03 if still needed).
