# BossMod AI — Day-one capability pass

Locked order: **(1) named-path open/read/edit** and **(2) jailed diagnostic CLI** shipped on `main` (PR #22). This note covers **(3) peer assign/deliver** combined with that host-roots model.

Public wording stays impersonal. Fixture agents are **Cap Assigner** and **Cap Worker**.

## Honesty bar

| Item | Status | What is proven | What is not proven |
| --- | --- | --- | --- |
| (1) Named path | Shipped | User-named path under `/me`, `/projects`, or a configured host root opens/reads/edits | Full unrestricted host mount |
| (2) Diagnostic CLI | Shipped | Pathless `uname` and allowed `ls`/`cat` under the jail; `/etc/passwd` denied | Shell enabled by default (`cli_shell_enabled` stays fail-closed) |
| (3) Peer assign/deliver on a host-path task | Proven here via the same APIs/actions/triggers the live loop calls | Cap Assigner owns a host-root task, assigns it to Cap Worker; Worker is woken, reads/edits under the allowlisted root, completes; assigner/operator can observe; `/etc/passwd` still denied | A true interactive dual-LLM GUI loop in this verification environment |

Host-roots / shell jail from PR #22 are unchanged. Personal `/me` + shared `/projects` remain; extra host FS is still opt-in allowlist.

## Item (3) — what shipped

1. **Host-path task.** Cap Assigner receives a task whose work contract is a file under Settings → CLI Policy → Host workspace roots (same allowlist as #22).
2. **Assign.** Cap Assigner `delegateTask`s that host file to Cap Worker (peer action). `POST /api/tasks` with `requester_id` remains the operator Assign Task path.
3. **Wake.** Worker gets a durable `task_assigned` row (`GET /api/agents/{id}/triggers`).
4. **Edit under the allowlisted root.** Worker `accept` → `cat` / `write` the named host path via the same `execute_action` `bm_cli` the live loop calls. The host file is the deliverable (not rewritten to `/projects`; `/me` peer outputs still rewrite).
5. **Observe.** Child task `complete`, host file content visible via Company Files, assigner `task_update` on the parent workstream.
6. **Deny stays closed.** Worker `cat /etc/passwd` is `bm_cli_error`; Company Files GET `/etc/passwd` is **400**. Assigning `/etc/passwd` as a work-contract path is **400** / `world_feedback`, not a 500.

Follow-up triggers persist through `persist_result_triggers` (same helper the dispatcher uses). Unknown `assigned_to` on `POST /api/tasks` is **404**.

## Item (3) — what did not ship

- A dual-LLM interactive GUI run (no models configured in this environment; turns would skip)
- Meetings/channels as the assign path
- Changing host-roots, path jail, Telegram fail-closed, or auth
- Treating an extra host root as a full home-directory mount

## Residual gaps

- The worker still needs a configured model to *choose* accept/write/complete. This pass proves the runtime path those choices call, not model quality.
- Operator Assign Task and `delegateTask` both wake the assignee; this note does not claim every chat/meeting paraphrase of “please do this” creates a peer task.
- `task_assigned` is queued even if the assignee is busy; dispatch still waits on movement (`in_transit`).
- After a child completes, the parent coordination task may stay `accepted` until the owner closes it. Observation is the parent `task_update` plus the host file.

## Live scenario (reproducible)

A true interactive dual-LLM GUI loop was **not** run. The same contracts the live loop calls were exercised:

```bash
uv run pytest -q tests/test_peer_assign_deliver.py
BOSSMOD_DB_PATH=/tmp/bossmod-cap-peer/bossmod.sqlite3 \
BOSSMOD_CAP_HOST_ROOT=/tmp/bossmod-cap-host \
  uv run python scripts/run_peer_assign_scenario.py
```

Captured `2026-09-05T21:16:15Z`:

| Step | Call | Result |
| --- | --- | --- |
| 0 roots | `PUT /api/settings/workspace_host_roots` = `/tmp/bossmod-cap-host` | **200**; fixture `review.py` starts as `print("before-review")` |
| 1 create | `POST /api/agents` Cap Assigner + Cap Worker | **201** / **201**; `b3f6f058-…` / `9a1ab6b5-…` |
| 2 own | `POST /api/tasks` assigned_to=Cap Assigner, deliverable=`/tmp/bossmod-cap-host/review.py` | **201** parent=`2aab14ae-3ee6-4a8b-b55a-8e108ab344e9` status=`pending` |
| 3 assign | `execute_action` `delegateTask` → Cap Worker, same host path | `status_changed` child=`fef5d64f-2a53-4fb2-9287-0c2b409fd7bf` path stays `/tmp/bossmod-cap-host/review.py` |
| 4 wake | `GET /api/agents/{worker}/triggers` | **200**; queued `task_assigned` `1595dfb0-…` from=`Cap Assigner` |
| 5 accept | `apply_decision` accept | `decision_applied`; GET child **200** status=`accepted` |
| 6 edit + deny | `bm_cli` `cat`/`write` host path; `cat /etc/passwd`; GET company `/etc/passwd` | cat/write `bm_cli_result`; file becomes `print("after-review")`; CLI deny `bm_cli_error` “outside the allowed workspace roots” / “not a full host mount”; company **400** |
| 7 deliver | `complete` + GET child/events/file/assigner triggers | GET **200** status=`complete`; company file **200** content `print("after-review")`; queued parent `task_update` on Cap Assigner |

Honest failures covered by pytest (not a GUI demo):

- Assignee **declines** → status=`declined`, no artifact, assigner `task_follow_up`
- Work-plan assignee name matches **more than one** teammate → parent is not accepted, no child
- Host-path work contract or `delegateTask` targeting `/etc/passwd` → **400** / `world_feedback`, no task row

## Items (1)+(2) — still true (PR #22)

1. **Named path open/read/edit.** `resolve_cli_path`, Company Files, and agent `cli` accept a user-named absolute path when it stays under `/me`, `/projects`, or Host workspace roots.
2. **Diagnostic CLI.** With shell enabled, pathless diagnostics such as `uname -a` run under the existing jail. Escapes such as `cat /etc/passwd` are denied. Approval does not jailbreak.

Operator setup for extra host roots is unchanged: one existing absolute directory per line; `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/root` rejected; empty setting = no extra host access; `cli_shell_enabled` stays fail-closed until opted in.
