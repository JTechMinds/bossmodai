---
schema_version: 3.0
last_updated_utc: 2026-09-25T21:08:59Z
head_commit: "c10e1ec28a2f5b79ba4f5c8d725a744644bc18bb"
scopes: ["/api", "/core", "/db", "/desktop", "/docs", "/integrations", "/plugins", "/prompts", "/scripts", "/tests", "/ui"]
---

# Project Memory Bank: BossMod AI

> Read this file first. For file-level detail, open only the detail file(s) in the Scope Map that match your task.

## 1. Project Summary
BossMod AI is a self-hosted, offline desktop platform for autonomous AI agent teams working in a visual 2D office. An operator hires agents with role contracts, talks to them in DMs and shared threads, assigns tasks, and approves or denies what they do through consent cards and a "Needs you" queue. Agents run structured decision and execution turns against LLMs through litellm. They act through a sandboxed virtual CLI (`bm_cli`) with DB-driven policy, a host-path jail and git fences. Floors isolate them from each other. Two processes share one SQLite database: a FastAPI app process that serves the API and UI, and a runtime worker process that runs the dispatcher, watchdogs and the world simulation. A Tauri shell wraps both, and a Telegram bot is an optional second front end.

## 2. Technology Stack
*   **Backend:** Python ≥3.12, FastAPI + uvicorn, Pydantic v2, Jinja2 (single page template), httpx, PyYAML, `pathfinding` (A* office movement); managed with `uv` (`uv.lock`)
*   **LLM:** litellm behind `core/llm/client.py`, with per-activation-mode model routing, a SQLite-backed global inflight call budget, and a separate "System AI" for short completions (routing, fades, sticky slots, auto-approve review)
*   **Datastore:** SQLite (`db/schema.sql`, 52 tables) with thread-local connections, a Postgres-style-to-SQLite param compat shim, additive migrations, and secret columns wrapped at rest (`bm1:`) with a chmod-600 data key
*   **Frontend:** Framework-free, build-less vanilla JS (classic scripts publishing `window.BossMod*` globals, loaded in order by `ui/templates/index.html`), token-driven CSS (`tokens.css`), and vendored tailwind Play, lucide, marked and highlight.js. No CDNs.
*   **Desktop:** Tauri v2 (Rust crate `bossmod-desktop`) that spawns `main.py` on 127.0.0.1:38471, runs the tray/taskbar Needs badge, and guards external URLs
*   **Integrations:** python-telegram-bot (fail-closed allowlist)
*   **Testing:** pytest + pytest-asyncio (asyncio_mode auto), plus Node `.cjs` harnesses that run UI modules against a shared fake DOM; `conftest.py` redirects all writable roots to temp dirs

## 3. Core Concepts & Data Models
*   **Agent / AgentState** (`/core` models/agent.py, `/db` agents.py): A hired AI worker. It has a role contract (specialty, description, done criteria), a communication contract, personality, model routing and a floor. Its identity is a durable storage key. Setup snapshots survive deletion and back the "Recent" list.
*   **Floor** (`/core` floors.py, `/db` floors.py): A hard isolation domain (Lobby by default). Agents, threads and projects belong to one floor. Floor co-mingle gates guard wake, assign and seat. Each floor maps to a company folder on disk.
*   **Channel (thread) / Message** (`/core` models/channel.py, message.py; `/db` channels.py, messages.py): Shared multi-agent threads and 1:1 DMs. Threads have host-owned Talk/Work/Paused state, response rounds (ordered or fan-out, System-AI routed), archive/reopen and CLI auto-approve.
*   **Task** (`/core` tasking/, `/db` tasks.py): Durable work with a status state machine (`tasking/transitions.py`), `closed_at`, work contract and deliverables, notification policy and target, and an event log. Status changes are mirrored as one-liners into the origin thread.
*   **AgentTrigger** (`/core` models/trigger.py, `/db` agent_triggers.py): Durable wake queue with leases and heartbeats. The runtime dispatcher claims triggers to run agent turns.
*   **Decision / Execution turn** (`/core` agent_loop/): Decision turns parse a structured envelope (say/actions/work_commit), may run CLI peeks, and materialize commitments. Execution turns run an action loop with CLI calls and guardian checks. Both fail closed with repair prompts from `/prompts`.
*   **Activity** (`/core` agent_loop/activity_runtime.py, `/db` activities.py): Runtime state machine for live work, meeting, movement and break.
*   **Meeting session** (`/core` agent_loop/meeting_*.py, `/db` meeting_*.py): Room or remote meetings with invites, a kickoff readiness check, and response rounds that share one implementation with channels.
*   **Runtime command / worker state** (`/core` runtime/, `/db` runtime_control.py): App-to-worker command queue plus worker heartbeat. Events flow back over the worker's stdout.
*   **CLI policy rule / approval request** (`/core` bm_cli/, `/db` cli_policy_rules.py, cli_approval_requests.py): Tiered allow/deny/approval_required rules, human approval queue, and nest-scoped "Always allow".
*   **Consent requests** (`/core` bm_cli/host_path_consent.py, workspace_preference.py, shell_executor_consent.py, nest_git_consent.py; `/db` host_path_consent.py): In-chat operator grants for host paths, workspace preference (clone/branch/edit-host), shell executor enablement and nest git credentials.
*   **Need / Notification** (`/api` routes/needs.py, `/db` notifications.py, `/ui` needs/): Aggregated "Needs you" queue of consent, approvals, auto-approve audits and blocked/stalled tasks. It surfaces in the bell, composer bar, toasts and OS badge.
*   **Agent pack / template** (`/core` agent_pack/, `/db` agent_templates.py): `bossmod.agent_pack/v1` YAML hire contracts from a pinned GitHub catalog, installed into a local template library or saved locally.
*   **AI connection / personality / settings** (`/db` ai_connections.py, ai_personalities.py, settings.py; `/core` config.py): Provider credentials (encrypted), seeded role personalities, and every tunable as a settings row (no hardcoded defaults). Prompt templates are seeded from `/prompts`.
*   **Artifact / virtual FS** (`/core` bm_cli/virtual_fs.py, `/db` artifacts.py): Agent `/me` workspace (auto-committed git) and shared `/projects`, mapped to disk under the company root, with an artifact registry.

## 4. Primary User/Data Flows
*   **App boot:**
    1.  `/desktop` src/main.rs: find project root, stop stale backend, spawn `.venv/bin/python main.py` in its own process group, poll `/health`
    2.  `main.py` (root): FastAPI app, `install_local_api_auth` from `/api` auth.py, `db.init_db` from `/db` connection.py (schema, migrations, seeds)
    3.  `/core` runtime/services.py: spawn and supervise the `core/runtime/worker.py` subprocess, which runs the dispatcher, watchdogs and simulation
    4.  `/ui` templates/index.html: render with injected API token; `shell/shell.js` boots store, bus and frame, then connects `/api/ws` last
*   **Operator message → agent reply:**
    1.  `/ui` conversation/composer.js → sources/agent-source.js or thread-source.js: POST agent or channel messages
    2.  `/api` routes/agents.py → `/core` messaging.py: persist, broadcast, start channel rounds or enqueue triggers (`/db` agent_triggers.py)
    3.  `/core` runtime/worker.py → agent_loop/dispatcher.py: claim trigger lease → loop.py `run_turn` → decision_turn.py / execution_turn.py
    4.  `/core` llm/context_builder.py + `/prompts` templates → llm/client.py (litellm) → decision_runtime.py applies commitments to `/db`
    5.  `/core` runtime/events.py → worker stdout → services.py → `/api` websocket.py `manager` broadcast → `/ui` shell/socket.js → core/bus.js → conversation transcript
*   **Agent CLI call with approval/consent:**
    1.  `/core` agent_loop/actions_cli.py → bm_cli/runtime.py: parse, check `policy_engine.py`, host-root jail, locked-clone outcome
    2.  `/core` bm_cli: on `approval_required` or missing consent, create a request in `/db` (cli_approval_requests / host_path_consent) and post a notification card in the origin thread
    3.  `/api` routes/needs.py `GET /api/needs` → `/ui` needs/needs-store.js → consent-card.js / needs-popover.js / `/desktop` tray badge
    4.  `/ui` POST `/api/cli-policy/approvals/{id}/approve` (or host-path-consent / shell-executor / nest-git) → `/api` routes → `/core` bm_cli/approvals.py resumes via runtime command
*   **Task assignment and delegation:**
    1.  `/ui` places/tasks/assign-form.js: POST `/api/tasks`
    2.  `/api` routes/tasks.py → `/core` tasking/service.py create-or-bind + agent_loop/role_contracts.py specialty ranking → activity_scheduler.py wake trigger
    3.  `/core` worker turns: accept, work, delegate (`actions_tasks.py`), and done/block (`actions_lifecycle.py`, checked by tool_evidence and shared_handoff)
    4.  `/core` agent_loop/task_origin_mirrors.py: status one-liners into the origin thread → `/ui` Tasks place and desk refresh via operator-invalidate
*   **Telegram operator:**
    1.  `/integrations` telegram/bot.py: allowlist check (auth.py), then commands or plain text → `/core` messaging.py (same ingress as the UI)
    2.  `/core` runtime/services.py `_dispatch_event` → `/integrations` telegram/bridge.py: push replies, channel messages and approval cards to session holders

## 5. Scope Map

- 📁 **/api** - FastAPI transport: `/api` REST routers, `/api/ws` WebSocket, local token auth, secret redaction · `fastapi, rest, websocket, auth` · 22 files · [detail](memory-bank/api.md)
  - 📁 **routes/** - Per-domain APIRouters aggregated under the `/api` prefix
- 📁 **/core** - Domain and runtime layer: agent turn loop, CLI sandbox, LLM integration, models, tasking, floors, world · `agent-loop, runtime, cli, llm, domain` · 187 files · [detail](memory-bank/core.md)
  - 📁 **agent_loop/** - Agent turn engine: triggers, decision/execution turns, channels, meetings, blocking, notifications
  - 📁 **agent_pack/** - Agent pack YAML schema, GitHub catalog import, and export
  - 📁 **bm_cli/** - Controlled agent CLI: virtual FS, policy, consent gates, shell sandbox, git
    - 📁 **managed_writer/** - LLM-managed file write, batch, and section rewrite
  - 📁 **llm/** - LLM client, routing, call budget, context assembly, templates
  - 📁 **models/** - Pydantic domain models
  - 📁 **prompting/** - Model-facing prompt surface registry and lint
  - 📁 **runtime/** - App/worker process split and event bridge
  - 📁 **tasking/** - Task service, board, resolution, and status transitions
  - 📁 **world/** - Office tilemap, pathfinding, seating, and movement simulation
- 📁 **/db** - Modular SQLite data layer: connection/schema/migrations, hardened CRUD helpers, one module per table group, `db.<fn>` façade · `sqlite, persistence, migrations, secrets` · 47 files · [detail](memory-bank/db.md)
- 📁 **/desktop** - Tauri v2 native shell: backend spawn/teardown, webview, Needs tray badge, external URL guard · `rust, tauri, desktop` · 7 files · [detail](memory-bank/desktop.md)
  - 📁 **icons/** - App/bundle icons; `icon.svg` is the vector source
  - 📁 **src/** - Rust sources for the shell binary
- 📁 **/docs** - Architecture, agent-pack format, security/health audits, design specs and implementation plans · `docs, architecture, specs, plans` · 37 files · [detail](memory-bank/docs.md)
  - 📁 **superpowers/** - Brainstormed design specs and executable implementation plans
    - 📁 **specs/** - Approved design specs (problem, decisions, data, API, UI, tests)
    - 📁 **plans/** - Step-by-step implementation plans (files, tests, verification per task)
- 📁 **/integrations** - External service integrations; currently the Telegram bot · `telegram, bot, integrations` · 8 files · [detail](memory-bank/integrations.md)
  - 📁 **telegram/** - Telegram bot: commands, event bridge, session routing, allowlist auth
- 📁 **/plugins** - Empty placeholder (`.gitkeep` only); nothing implemented · `placeholder` · 1 file · [detail](memory-bank/plugins.md)
- 📁 **/prompts** - Seed Markdown prompt templates loaded by `core/default_prompts.py` into settings · `prompts, templates, personalities` · 54 files · [detail](memory-bank/prompts.md)
  - 📁 **internal/** - Runtime-injected agent-loop, CLI-result, repair, action-guidance, and managed-writer prompts
  - 📁 **personalities/** - Seed role personalities keyed by role name
- 📁 **/scripts** - Manual dev/QA utilities: smoke suites, desktop icon generation, peer-assign scenario runner · `scripts, smoke-test, tooling` · 4 files · [detail](memory-bank/scripts.md)
- 📁 **/tests** - Pytest suite plus Node `.cjs` UI harnesses over a shared fake DOM · `pytest, js-harness, testing` · 230 files · [detail](memory-bank/tests.md)
  - 📁 **fixtures/** - Static test inputs
    - 📁 **agent_mp/** - Fixture agent-pack marketplace catalog and packs
- 📁 **/ui** - Operator frontend: one Jinja page plus build-less JS modules and token-driven CSS · `frontend, vanilla-js, css, websocket` · 203 files · [detail](memory-bank/ui.md)
  - 📁 **templates/** - Server-rendered page shell
  - 📁 **static/** - Static assets
    - 📁 **css/** - Token-driven stylesheets
    - 📁 **js/** - Classic-script modules exposing `window.BossMod*` globals
