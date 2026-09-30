---
schema_version: 3.0
last_updated_utc: 2026-09-30T14:15:00Z
head_commit: "af65a17ac3d4048a2c493f04ec17e6a9459b9fa5"
scopes: ["/api", "/core", "/db", "/desktop", "/extensions", "/integrations", "/plugins", "/prompts", "/scripts", "/tests", "/ui"]
---

# Project Memory Bank: BossMod AI

> Read this file first. For file-level detail, open only the detail file(s) in the Scope Map that match your task.

## 1. Project Summary
BossMod AI is a self-hosted, offline desktop platform where teams of autonomous AI agents work in a visual 2D office.

- **The operator:** hires agents with role contracts, talks to them in DMs and shared threads, and assigns tasks. They approve or deny what agents do through consent cards and a "Needs you" queue.
- **The agents:** run structured decision and execution turns against LLMs through litellm. They act through a sandboxed virtual CLI (`bm_cli`) with DB-driven policy, a host-path jail and git fences.
- **Extensions:** optional and manifest-driven. Browser Vision handles web browsing; Microsoft 365 mail adds a per-agent mailbox and wakes the agent on new mail.
- **Floors:** isolate agents, threads and projects from each other.
- **Processes:** two processes share one SQLite database. A FastAPI app process serves the API and UI. A runtime worker process runs the dispatcher, watchdogs, world simulation, extension hosts and the extension wake service.
- **Front ends:** a Tauri shell wraps both processes, and a Telegram bot is an optional second front end.

## 2. Technology Stack
*   **Backend:** Python ≥3.12, FastAPI + uvicorn, Pydantic v2, Jinja2 (single page template), httpx, PyYAML, `pathfinding` (A* office movement); managed with `uv` (`uv.lock`)
*   **LLM:** litellm behind `core/llm/client.py`, with:
    *   per-activation-mode model routing
    *   a SQLite-backed global inflight call budget
    *   multimodal attachment parts gated by per-model image capability
    *   a separate "System AI" for short completions: channel routing, chat fade, sticky slots, idle checks, auto-approve review
*   **Datastore:** SQLite (`db/schema.sql`, 60 tables) with per-thread connections, additive in-code migrations and startup seeding. Secret columns are wrapped at rest (`bm1:`) with a file data key.
*   **Frontend:** framework-free, build-less vanilla JS: classic scripts publish `window.BossMod*` globals and are loaded in order by `ui/templates/index.html`.
    *   Styling is token-driven CSS (`tokens.css`).
    *   Secondary screens use one layered modal frame, `core/overlays.js`, with a breadcrumb trail from `core/modal-trail.js`.
    *   Vendored libraries: tailwind Play, lucide, marked, highlight.js and Tabulator. No CDNs.
*   **Desktop:** Tauri v2 (Rust crate `bossmod-desktop`). It spawns `main.py` on 127.0.0.1:38471, runs the tray/taskbar Needs badge, guards external URLs, and reads clipboard images for composer paste.
*   **Extensions:** Playwright/Chromium + Pillow (`browser-vision`); Microsoft Graph over httpx with OAuth2 client credentials (`ms365-mail`)
*   **Integrations:** python-telegram-bot (fail-closed allowlist)
*   **Testing:** pytest + pytest-asyncio, plus Node `.cjs` harnesses that run real UI modules against a shared fake DOM.
    *   `conftest.py` redirects all writable roots to temp dirs, and `_real_data_guard.py` refuses real data.
    *   No live LLM.
    *   A 400-line per-module cap is enforced by tests.

## 3. Core Concepts & Data Models
*   **Agent / AgentState** (`/core` models/agent.py, agent_repository.py; `/db` agents.py): a hired AI worker.
    *   It has a role contract (specialty, description, done criteria), a closed-enum communication contract, a personality, model routing and a floor.
    *   Identity is an immutable storage key from a ledger whose keys are never reissued.
    *   Secret-free setup snapshots survive deletion and back the "Recent" list.
*   **Floor** (`/core` floors.py, floor_moves.py; `/db` floors.py): a hard isolation domain; the Lobby always exists.
    *   Agents, threads and projects each belong to one floor.
    *   Gates deny cross-floor wake, assign and seat.
    *   Each floor maps to a company folder on disk.
*   **Channel (thread) / Message** (`/core` models/channel.py, messaging.py; `/db` channels.py, messages.py): shared multi-agent threads and 1:1 DMs.
    *   Threads have a host-owned Talk/Paused state and System-AI-routed response rounds.
    *   They also support archive/reopen, stale-reply supersede, and per-thread CLI auto-approve.
*   **Attachment** (`/core` attachments.py, llm/attachment_parts.py; `/db` attachments.py): uploaded message files scoped to a conversation and floor. They are expanded into model content parts when the routed model supports images.
*   **Task** (`/core` tasking/, `/db` tasks.py): durable work.
    *   Status changes follow an enforced state machine (`tasking/transitions.py`).
    *   It carries `closed_at`, a work contract with deliverables, a notification policy/target, and an event log.
    *   Status changes are mirrored as one-liners into the origin thread.
*   **Work binding** (`/core` agent_loop/work_binding.py): a turn-scoped answer to which live work or task a turn may act on. Turns detached from live work see none.
*   **AgentTrigger** (`/core` models/trigger.py, `/db` agent_triggers.py): a durable wake queue with leases, heartbeats and stale requeue. The runtime dispatcher claims triggers to run agent turns under the call budget. Extension wakes arrive as coalesced `extension_event` triggers.
*   **Decision / Execution turn** (`/core` agent_loop/):
    *   Decision turns parse a structured envelope (say/actions/`work_commit`), may run budgeted CLI peeks, and materialize commitments.
    *   Execution turns run an action loop with CLI calls, guardian checks, and work snapshots frozen across pauses.
    *   Both fail closed with repair prompts from `/prompts`.
*   **Activity / Meeting session** (`/core` agent_loop/activity_runtime.py, meeting_*.py; `/db` activities.py, meeting_*.py): the runtime state machine for work, meetings, movement and breaks. Room or remote meetings share round logic with channels.
*   **Runtime command / worker state** (`/core` runtime/, `/db` runtime_control.py): the app-to-worker command queue plus the worker heartbeat. Events flow back as JSONL over the worker's stdout.
*   **CLI policy rule / approval / consent requests** (`/core` bm_cli/, `/db` cli_policy_rules.py, cli_approval_requests.py, host_path_consent.py):
    *   Tiered allow/deny/approval_required rules and a human approval queue.
    *   In-chat operator grants for host paths, workspace preference, shell executor and nest git credentials.
*   **Need / Notification** (`/api` routes/needs.py, `/db` notifications.py, `/ui` needs/): the aggregated "Needs you" queue of consent, approvals and blocked or stalled tasks. It surfaces in the bell, the composer bar, toasts and the OS badge.
*   **Extension** (`/core` extensions/, `/extensions`, `/db` extension_agent_configs.py): a manifest-driven plugin (manifest.json, prompt.md, and a `create(ctx)` package), loaded only when enabled.
    *   It contributes a CLI command via the CLI bridge, a prompt block, and optional setup.
    *   It can also provide a live view, per-agent encrypted config, agent views, and a `wake` poll (`core/extensions/wake_service.py`).
*   **Agent pack / template** (`/core` agent_pack/, `/db` agent_templates.py): `bossmod.agent_pack/v1` YAML hire contracts from a pinned, allowlisted GitHub catalog. They are installed into a local template library or saved locally.
*   **AI connection / personality / settings** (`/db` ai_connections.py, ai_personalities.py, settings.py; `/core` config.py):
    *   Provider credentials (encrypted), per-model capabilities, and seeded role personalities.
    *   Every tunable is a settings row; there are no hardcoded defaults.
    *   Prompt templates are seeded from `/prompts`.
*   **Artifact / virtual FS** (`/core` bm_cli/virtual_fs.py, workspace_git.py; `/db` artifacts.py; `/api` routes/_desk.py): the agent's `/me` workspace (auto-committed git) and a shared per-floor `/projects`, mapped to disk under the company root, with an artifact registry.
*   **Modal layer / trail** (`/ui` core/overlays.js, core/modal-trail.js): the one navigation model for secondary screens.
    *   A modal opened from a modal becomes a layer.
    *   The head shows a breadcrumb trail of layers plus in-dialog steps (`setSteps`).
    *   ‹, Esc and crumbs go back; ✕ closes all; `closeFrom()` closes a layer and everything above it.
*   **Agent desk** (`/ui` context/desk-dialog.js, desk-panel.js): one agent's panel modal, opened from any place via the injected `openDesk(agentId, path?)`. It shows Tasks, Files, About, Details, Notes and Extensions. Its head holds the Chat and Edit role tools plus a `⋯` menu.

## 4. Primary User/Data Flows
*   **App boot:**
    1.  `/desktop` src/main.rs: find the project root, stop any stale backend, spawn `.venv/bin/python main.py` in its own process group, then poll `/health`
    2.  `main.py` (root): the FastAPI app, the `/api` auth.py token gate and settings-refresh middleware, and `/db` connection.py `init_db` (schema, migrations, seeds)
    3.  `/core` runtime/services.py: spawn and supervise `core/runtime/worker.py`, which runs the dispatcher, watchdogs, channel idle check, simulation, enabled extensions and the extension wake service
    4.  `/ui` templates/index.html: render with the injected API token. `shell/shell.js` builds the store, bus, desk dialog, navigator, frame and needs, and connects `/api/ws` last
*   **Operator message → agent reply:**
    1.  `/ui` conversation/composer.js → sources/agent-source.js or thread-source.js: POST agent or channel messages with attachment ids
    2.  `/api` routes/agents.py → `/core` messaging.py: persist, broadcast, then start channel rounds (channel_rounds.py / channel_router.py) or enqueue triggers (`/db` agent_triggers.py)
    3.  `/core` runtime/worker.py → agent_loop/dispatcher.py: claim the trigger lease → loop.py `run_turn` (work_binding.py scopes the turn) → decision_turn.py / execution_turn.py
    4.  `/core` llm/context_builder.py + `/prompts` templates → llm/client.py (litellm) → decision_runtime.py applies commitments to `/db`
    5.  `/core` runtime/events.py → worker stdout → services.py → `/api` websocket.py `manager` broadcast → `/ui` shell/socket.js → core/bus.js → conversation transcript
*   **Agent CLI call with approval/consent:**
    1.  `/core` agent_loop/actions_cli.py → bm_cli/runtime.py: parse, check `policy_engine.py`, the host-root jail, and the locked-clone outcome
    2.  `/core` bm_cli: on `approval_required` or missing consent, create a request in `/db` and post a notification card in the origin thread (agent_loop/notifications.py)
    3.  `/api` routes/needs.py `GET /api/needs` → `/ui` needs/needs-store.js → core/consent-card.js / needs-popover.js / `/desktop` tray badge
    4.  `/ui` POST approve/deny (cli-policy approvals, host-path-consent, shell-executor, workspace-preference, nest-git) → `/api` routes → `/core` bm_cli/approvals.py resumes via a runtime command
*   **Task assignment and delegation:**
    1.  `/ui` places/tasks/assign-form.js: POST `/api/tasks`
    2.  `/api` routes/tasks.py → `/core` tasking/service.py create-or-bind + agent_loop/role_contracts.py specialty ranking → activity_scheduler.py wake trigger
    3.  `/core` worker turns: accept, work, delegate (`actions_tasks.py`), done/block (`actions_lifecycle.py`, checked by tool_evidence.py and shared_handoff.py)
    4.  `/core` agent_loop/task_origin_mirrors.py: status one-liners into the origin thread → `/ui` Tasks place and desk rows refresh via operator-invalidate
*   **Agent desk and modal navigation:**
    1.  `/ui` roster, Office, mentions, conversation header, "Open in Desk" notes or mini-office seat → `ctx.openDesk(agentId, path?)` (context/desk-dialog.js, built once in shell.js)
    2.  `/ui` desk-panel.js composes the desk-tasks, desk-files, desk-notes, desk-extensions and desk-actions sections → `/api` `/api/agents/{id}`, `/api/agents/{id}/desk`, `/api/tasks/board`
    3.  `/ui` a task row → desk-task-opener.js → places/tasks/task-layers.js opens the Task detail as a layer. File viewer, Edit role, extension settings and confirms also stack as layers, and core/overlays.js + modal-trail.js render the trail
    4.  `/ui` leaving the desk (See all, Diagnostics, Chat) or the agent's removal → `closeFrom()` closes the desk and everything above it, then navigates
*   **Extension command and wake (e.g. Browser Vision, MS365 mail):**
    1.  `/ui` extensions/extensions-dialog.js → `/api` routes/extensions.py: enable (writes `extensions_enabled`), then run one-click setup (`core/extensions/setup_runner.py`)
    2.  `/core` extensions/registry.py + loader.py: discover manifests and import enabled packages in the worker; prompt_blocks.py adds the extension prompt
    3.  `/core` bm_cli/runtime.py → extensions/cli_bridge.py → `/extensions` `bv …` (browser_host.py, Playwright) or `mail …` (Microsoft Graph) → the CLI result
    4.  `/core` extensions/wake_service.py polls `wake` extensions (for example `ms365-mail` wake.py new-mail batches) → coalesced `extension_event` trigger → `/prompts` runtime_block_trigger_event.md
*   **Telegram operator:**
    1.  `/integrations` telegram/bot.py: allowlist check (auth.py), then commands or plain text → `/core` messaging.py (the same ingress as the UI)
    2.  `/core` runtime/services.py `_dispatch_event` → `/integrations` telegram/bridge.py: push replies, channel messages and approval cards to session holders

## 5. Scope Map

- 📁 **/api** - App-process HTTP/WS transport: `/api` REST routers, `/api/ws` WebSocket, local token auth, secret redaction · `fastapi, rest, websocket, auth` · 25 files · [detail](memory-bank/api.md)
  - 📁 **routes/** - REST routers aggregated under the `/api` prefix
- 📁 **/core** - Domain core: agent runtime loop, CLI sandbox, LLM, tasks, floors, world, extension host · `agent-loop, runtime, cli, llm, extensions` · 201 files · [detail](memory-bank/core.md)
  - 📁 **agent_loop/** - Agent turn engine: dispatcher, decision/execution turns, channel/meeting rounds, notifications
  - 📁 **agent_pack/** - Agent pack (hire contract) schema, catalog, GitHub import/export
  - 📁 **bm_cli/** - Controlled virtual CLI for agents: commands, policy, consent gates, shell sandbox, git
    - 📁 **managed_writer/** - LLM-managed file/section authoring behind the CLI
  - 📁 **extensions/** - Extension host: discovery, manifest, loader, CLI bridge, prompt blocks, wake service
  - 📁 **llm/** - LLM client, routing, context assembly, call budget, templating
  - 📁 **models/** - Pydantic domain and API models
  - 📁 **prompting/** - Model-facing prompt surface registry and lint
  - 📁 **runtime/** - App/runtime-worker process split and event bridge
  - 📁 **tasking/** - Task service, board views, status transitions
  - 📁 **world/** - Office tilemap, pathfinding, movement simulation
- 📁 **/db** - SQLite persistence package: connection/schema/migrations, hardened CRUD helpers, per-domain stores behind a `db.<fn>` façade · `sqlite, persistence, migrations, secrets` · 53 files · [detail](memory-bank/db.md)
- 📁 **/desktop** - Tauri v2 native shell: backend spawn/teardown, webview, Needs tray badge, external URL guard, clipboard image read · `rust, tauri, desktop` · 8 files · [detail](memory-bank/desktop.md)
  - 📁 **icons/** - App/bundle icons (png, icns, ico); `icon.svg` is the vector source
  - 📁 **gen/schemas/** - Tauri-generated ACL/capability JSON schemas (generated; not indexed)
  - 📁 **src/** - Rust sources for the shell binary
- 📁 **/extensions** - Optional manifest-driven agent capabilities loaded by the extension host when enabled · `extensions, playwright, microsoft-graph, wake` · 25 files · [detail](memory-bank/extensions.md)
  - 📁 **browser-vision/** - `bv` command: agents browse websites by screenshot, element marks, pointer and 3x3 keypad grid
  - 📁 **ms365-mail/** - `mail` command: per-agent Microsoft 365 mailbox via Microsoft Graph, with wake-on-new-mail
- 📁 **/integrations** - External service integrations; currently the Telegram bot · `telegram, bot, integrations` · 8 files · [detail](memory-bank/integrations.md)
  - 📁 **telegram/** - Telegram bot: commands, event bridge, session routing, allowlist auth
- 📁 **/plugins** - Empty placeholder (`.gitkeep` only); nothing implemented · `placeholder` · 1 file · [detail](memory-bank/plugins.md)
- 📁 **/prompts** - Default Markdown prompt templates loaded by `core/default_prompts.py` into settings · `prompts, templates, personalities` · 56 files · [detail](memory-bank/prompts.md)
  - 📁 **internal/** - Runtime-injected follow-up, repair, CLI-result and managed-writer snippets (keys prefixed `internal_`)
  - 📁 **personalities/** - Seeded role personality templates with decision/execution-conditional guidance
- 📁 **/scripts** - Manual dev/QA utilities: smoke suites, desktop icon generation, peer-assign scenario runner · `scripts, smoke-test, tooling` · 4 files · [detail](memory-bank/scripts.md)
- 📁 **/tests** - Pytest suite plus Node `.cjs` UI harnesses over a shared fake DOM · `pytest, js-harness, testing` · 268 files · [detail](memory-bank/tests.md)
  - 📁 **fixtures/** - Static test fixtures
    - 📁 **agent_mp/** - Fake agent-pack marketplace (catalog + packs) for pack/template/contract tests
- 📁 **/ui** - Operator frontend: one Jinja page plus build-less classic-script JS modules and token-driven CSS · `frontend, vanilla-js, css, websocket, modals` · 220 files · [detail](memory-bank/ui.md)
  - 📁 **templates/** - Server-rendered page shell
  - 📁 **static/** - Static assets
    - 📁 **css/** - Token-driven stylesheets (no hex outside tokens.css); `css/vendor/` vendored, not indexed
    - 📁 **js/** - Classic-script modules exposing `window.BossMod*` globals (no build step, no ES imports)
