# BossMod AI — Day-one capability pass

Locked order: **(1) named-path open/read/edit** and **(2) jailed diagnostic CLI** shipped on `main` (PR #22). This note now covers **(3) peer assign/deliver**.

Public wording stays impersonal. Fixture agents in the item-(3) proof are **Cap Assigner** and **Cap Worker**.

## Honesty bar

| Item | Status | What is proven | What is not proven |
| --- | --- | --- | --- |
| (1) Named path | Shipped | User-named path under `/me`, `/projects`, or a configured host root opens/reads/edits | Full unrestricted host mount |
| (2) Diagnostic CLI | Shipped | Pathless `uname` and allowed `ls`/`cat` under the jail; `/etc/passwd` denied | Shell enabled by default (`cli_shell_enabled` stays fail-closed) |
| (3) Peer assign/deliver | Proven here via the same APIs/actions/triggers the live loop calls | One agent assigns; assignee is woken; assignee accepts, writes a shared artifact, and completes; assigner can observe status + artifact | A true interactive dual-LLM GUI loop in this verification environment |

Host-roots / shell jail from PR #22 are unchanged. Personal `/me` + shared `/projects` remain; extra host FS is still opt-in allowlist.

## Item (3) — what shipped

The assign → wake → accept → deliver loop already existed as separate surfaces (`POST /api/tasks`, `delegateTask`, `apply_decision`, `execute_action` `bm_cli`/`complete`, `task_assigned` / `task_update` / `task_follow_up`). This pass makes that loop **reproducible and observable**:

1. **Assign.** Cap Assigner → Cap Worker via `delegateTask` (peer action) or `POST /api/tasks` with `requester_id` + `assigned_to` (same create/bind + wake as the Assign Task UI).
2. **Wake.** Assignee receives a durable `task_assigned` row. Operators can read it at `GET /api/agents/{id}/triggers`.
3. **Work + deliver.** Assignee `accept` (decision runtime) → `write` the contract file → `complete`. Peer file deliverables declared under `/me/...` are rewritten to `/projects/<project>/<task-id>/...` so the assigner can read them.
4. **Observe.** Task status `complete`, task-thread events, assigner `task_update` trigger, and the company-files artifact.

Follow-up triggers from actions/decisions persist through `persist_result_triggers` — the same helper the dispatcher uses after a successful turn.

`POST /api/tasks` now rejects an unknown `assigned_to` with **404** even when no work contract is attached (previously only checked when a contract was present).

## Item (3) — what did not ship

- A dual-LLM interactive GUI run (no models configured in this environment; turns would skip)
- Meetings/channels as the assign path (they can host work, but this proof uses tasking + Assign Task / `delegateTask`)
- Changing host-roots, path jail, Telegram fail-closed, or auth

## Residual gaps

- The worker still needs a configured model to *choose* accept/write/complete on its own. This pass proves the runtime path those choices call, not model quality.
- Operator Assign Task and `delegateTask` both wake the assignee; this note does not claim every chat/meeting paraphrase of “please do this” creates a peer task.
- `task_assigned` is queued even if the assignee is busy; dispatch still waits on movement (`in_transit`).

## Live scenario (reproducible)

A true interactive dual-LLM GUI loop was **not** run. The same contracts the live loop calls were exercised:

```bash
# isolated DB, then either:
uv run pytest -q tests/test_peer_assign_deliver.py
# or the HTTP + action script:
BOSSMOD_DB_PATH=/tmp/bossmod-cap-peer/bossmod.sqlite3 \
  uv run python scripts/run_peer_assign_scenario.py
```

Happy path captured by `scripts/run_peer_assign_scenario.py` (`2026-09-05T21:13Z`):

| Step | Call | Result |
| --- | --- | --- |
| 1 create | `POST /api/agents` Cap Assigner + Cap Worker | **201** / **201**; ids `3e3d1921-b5f3-4f72-98ad-8403978c33a5` / `e9d8990f-e715-40e0-b8b4-26ffdaf80c94` |
| 2 assign | `POST /api/tasks` requester=Cap Assigner, assigned_to=Cap Worker, `/me/status-note.md` | **201** `create_new_task` status=`pending` task=`0064a1d4-eb82-44f2-9fd8-3c9fcf8ebc3d`; path `/projects/cap-peer/0064a1d4-eb82-44f2-9fd8-3c9fcf8ebc3d/status-note.md` |
| 3 wake | `GET /api/agents/{worker}/triggers` | **200**; queued `task_assigned` from=`Cap Assigner` |
| 4 accept | `apply_decision` accept on that `task_assigned` | `decision_applied`; GET task **200** status=`accepted` |
| 5 deliver | `execute_action` `bm_cli write` + `complete` | `bm_cli_result` + `status_changed`; GET task **200** status=`complete` |
| 6 observe | events + company file + assigner triggers | **200** (6 events: pending→accepted, accepted→complete, completion); file **200** content `# Cap status note` / `Peer assign/deliver loop completed.`; queued `task_update` on Cap Assigner with `task_status=complete` |

Honest failures covered by pytest (not claimed as a GUI demo):

- Assignee **declines** → status=`declined`, no artifact, assigner gets `task_follow_up`
- Work-plan assignee name matches **more than one** teammate → parent is not accepted, no child task

## Items (1)+(2) — still true (PR #22)

**Scope of that pass:** named-path + jailed CLI only.

1. **Named path open/read/edit.** `resolve_cli_path`, Company Files, and agent `cli` (`cat` / `write` / …) accept a user-named absolute path when it resolves under `/me`, `/projects`, or Settings → CLI Policy → **Host workspace roots**.
2. **Diagnostic CLI.** With shell enabled, pathless diagnostics such as `uname -a` run under the existing jail/policy. Escapes such as `cat /etc/passwd` / `head /etc/passwd` are denied. Approval does not jailbreak.

Operator setup for extra host roots is unchanged: one existing absolute directory per line; `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/root` rejected; empty setting = no extra host access; `cli_shell_enabled` stays fail-closed until opted in.
