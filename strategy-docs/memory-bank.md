---
schema_version: 3.0
last_updated_utc: 2026-09-29T17:27:52Z
head_commit: "688ea32a7f301bb3e05aaa67320f5010c6f45d0e"
scopes: ["/api", "/core", "/db", "/desktop", "/extensions", "/integrations", "/plugins", "/prompts", "/scripts", "/tests", "/ui"]
---

# Project Memory Bank: BossMod AI

> Read this file first. For file-level detail, open only the detail file(s) in the Scope Map that match your task.

## 1. Project Summary
BossMod AI is a self-hosted, offline desktop platform for autonomous AI agent teams working in a visual 2D office. An operator hires agents with role contracts, talks to them in DMs and shared threads, assigns tasks, and approves or denies what they do through consent cards and a "Needs you" queue. Agents run structured decision and execution turns against LLMs through litellm. They act through a sandboxed virtual CLI (`bm_cli`) with DB-driven policy, a host-path jail and git fences, plus optional manifest-driven extensions (Browser Vision web browsing, Microsoft 365 mailbox). Floors isolate agents from each other. Two processes share one SQLite database: a FastAPI app process that serves the API and UI, and a runtime worker process that runs the dispatcher, watchdogs, world simulation and extension hosts. A Tauri shell wraps both, and a Telegram bot is an optional second front end.

## 2. Technology Stack
*   **Backend:** Python ≥3.12, FastAPI + uvicorn, Pydantic v2, Jinja2 (single page template), httpx, PyYAML, `pathfinding` (A* office movement); managed with `uv` (`uv.lock`)
*   **LLM:** litellm behind `core/llm/client.py`, with per-activation-mode model routing, a SQLite-backed global inflight call budget, multimodal attachment parts gated by per-model image capability, and a separate "System AI" for short completions (channel routing, chat fade, sticky slots, idle checks, auto-approve review)
*   **Datastore:** SQLite (`db/schema.sql`, 59 tables) with per-thread connections, additive in-place migrations, startup seeding, and secret columns wrapped at rest (`bm1:`) with a file data key
*   **Frontend:** Framework-free, build-less vanilla JS (classic scripts publishing `window.BossMod*` globals, loaded in order by `ui/templates/index.html`), token-driven CSS (`tokens.css`), and vendored tailwind Play, lucide, marked, highlight.js and Tabulator. No CDNs.
*   **Desktop:** Tauri v2 (Rust crate `bossmod-desktop`) that spawns `main.py` on 127.0.0.1:38471, runs the tray/taskbar Needs badge, guards external URLs, and reads clipboard images for composer paste (`tauri-plugin-clipboard-manager`, `png`)
*   **Extensions:** Playwright/Chromium + Pillow (`browser-vision`); Microsoft Graph over httpx with OAuth2 client credentials (`ms365-mail`)
*   **Integrations:** python-telegram-bot (fail-closed allowlist)
*   **Testing:** pytest + pytest-asyncio, plus Node `.cjs` harnesses that run real UI modules against a shared fake DOM; `conftest.py` redirects all writable roots to temp dirs and `_real_data_guard.py` refuses real data; no live LLM

## 3. Core Concepts & Data Models
*   **Agent / AgentState** (`/core` models/agent.py, agent_repository.py; `/db` agents.py): A hired AI worker with a role contract (specialty, description, done criteria), closed-enum communication contract, personality, model routing and a floor. Identity is an immutable storage key from a never-reissued ledger; secret-free setup snapshots survive deletion and back the "Recent" list.
*   **Floor** (`/core` floors.py, floor_moves.py; `/db` floors.py): A hard isolation domain (Lobby guaranteed). Agents, threads and projects belong to one floor; gates deny cross-floor wake, assign and seat. Each floor maps to a company folder on disk (`core/bm_cli/floor_roots.py`).
*   **Channel (thread) / Message** (`/core` models/channel.py, message.py, messaging.py; `/db` channels.py, messages.py): Shared multi-agent threads and 1:1 DMs. Threads have host-owned Talk/Paused state, System-AI-routed response rounds (ordered or fan-out), archive/reopen, stale-reply supersede and per-thread CLI auto-approve.
*   **Attachment** (`/core` attachments.py, llm/attachment_parts.py; `/db` attachments.py): Uploaded message files scoped to a conversation and floor, expanded into model content parts when the routed model supports images.
*   **Task** (`/core` tasking/, `/db` tasks.py): Durable work with an enforced status state machine (`tasking/transitions.py`), `closed_at`, work contract and deliverables, notification policy/target, and an event log. Status changes mirror as one-liners into the origin thread.
*   **AgentTrigger** (`/core` models/trigger.py, `/db` agent_triggers.py): Durable wake queue with leases, heartbeats and stale requeue. The runtime dispatcher claims triggers to run agent turns under the call budget.
*   **Decision / Execution turn** (`/core` agent_loop/): Decision turns parse a structured envelope (say/actions/`work_commit`), may run budgeted CLI peeks, and materialize commitments. Execution turns run an action loop with CLI calls, guardian checks and frozen work snapshots across pauses. Both fail closed with repair prompts from `/prompts`.
*   **Activity** (`/core` agent_loop/activity_runtime.py, `/db` activities.py): Runtime state machine for live work, meetings, movement and breaks.
*   **Meeting session** (`/core` agent_loop/meeting_*.py, `/db` meeting_*.py): Room or remote meetings with invites, a kickoff readiness check, and response rounds that share one implementation with channels.
*   **Runtime command / worker state** (`/core` runtime/, `/db` runtime_control.py): App-to-worker command queue plus worker heartbeat. Events flow back as JSONL over the worker's stdout.
*   **CLI policy rule / approval request** (`/core` bm_cli/, `/db` cli_policy_rules.py, cli_approval_requests.py): Tiered allow/deny/approval_required rules, human approval queue, nest-scoped "Always allow" and locked-clone shell outcomes.
*   **Consent requests** (`/core` bm_cli/host_path_consent.py, workspace_preference.py, shell_executor_consent.py, nest_git_consent.py; `/db` host_path_consent.py): In-chat operator grants for host paths, workspace preference (clone/branch/edit-host), shell executor enablement and nest git credentials.
*   **Need / Notification** (`/api` routes/needs.py, `/db` notifications.py, `/ui` needs/): Aggregated "Needs you" queue of consent, approvals and blocked/stalled tasks. It surfaces in the bell, composer bar, toasts and the OS badge.
*   **Extension** (`/core` extensions/, `/extensions`, `/db` extension_agent_configs.py): Manifest-driven plugin (manifest.json + prompt.md + `create(ctx)` package) loaded only when enabled. It contributes a CLI command via the CLI bridge, a prompt block, optional setup, live view, per-agent encrypted config and agent views.
*   **Agent pack / template** (`/core` agent_pack/, `/db` agent_templates.py): `bossmod.agent_pack/v1` YAML hire contracts from a pinned, allowlisted GitHub catalog, installed into a local template library or saved locally.
*   **AI connection / personality / settings** (`/db` ai_connections.py, ai_personalities.py, settings.py; `/core` config.py): Provider credentials (encrypted), per-model capabilities, seeded role personalities, and every tunable as a settings row (no hardcoded defaults). Prompt templates are seeded from `/prompts`.
*   **Artifact / virtual FS** (`/core` bm_cli/virtual_fs.py, workspace_git.py; `/db` artifacts.py): Agent `/me` workspace (auto-committed git) and shared per-floor `/projects`, mapped to disk under the company root, with an artifact registry.

## 4. Primary User/Data Flows
*   **App boot:**
    1.  `/desktop` src/main.rs: find project root, stop stale backend, spawn `.venv/bin/python main.py` in its own process group, poll `/health`
    2.  `main.py` (root): FastAPI app, `/api` auth.py token gate and settings-refresh middleware, `db` connection.py `init_db` (schema, migrations, seeds)
    3.  `/core` runtime/services.py: spawn and supervise `core/runtime/worker.py`, which runs the dispatcher, watchdogs, channel idle check, simulation and enabled extensions
    4.  `/ui` templates/index.html: render with injected API token; `shell/shell.js` boots store, bus, frame and needs, then connects `/api/ws` last
*   **Operator message → agent reply:**
    1.  `/ui` conversation/composer.js (+ composer-attachments.js) → sources/agent-source.js or thread-source.js: POST agent or channel messages with attachment ids
    2.  `/api` routes/agents.py → `/core` messaging.py: persist, broadcast, start channel rounds (channel_rounds.py / channel_router.py) or enqueue triggers (`/db` agent_triggers.py)
    3.  `/core` runtime/worker.py → agent_loop/dispatcher.py: claim trigger lease → loop.py `run_turn` → decision_turn.py / execution_turn.py
    4.  `/core` llm/context_builder.py + `/prompts` templates → llm/client.py (litellm) → decision_runtime.py applies commitments to `/db`
    5.  `/core` runtime/events.py → worker stdout → services.py → `/api` websocket.py `manager` broadcast → `/ui` shell/socket.js → core/bus.js → conversation transcript
*   **Agent CLI call with approval/consent:**
    1.  `/core` agent_loop/actions_cli.py → bm_cli/runtime.py: parse, check `policy_engine.py`, host-root jail, locked-clone outcome
    2.  `/core` bm_cli: on `approval_required` or missing consent, create a request in `/db` (cli_approval_requests / host_path_consent) and post a notification card in the origin thread (agent_loop/notifications.py)
    3.  `/api` routes/needs.py `GET /api/needs` → `/ui` needs/needs-store.js → core/consent-card.js / needs-popover.js / `/desktop` tray badge
    4.  `/ui` POST `/api/cli-policy/approvals/{id}/approve` (or host-path-consent / shell-executor / workspace-preference / nest-git) → `/api` routes → `/core` bm_cli/approvals.py resumes via runtime command
*   **Task assignment and delegation:**
    1.  `/ui` places/tasks/assign-form.js: POST `/api/tasks`
    2.  `/api` routes/tasks.py → `/core` tasking/service.py create-or-bind + agent_loop/role_contracts.py specialty ranking → activity_scheduler.py wake trigger
    3.  `/core` worker turns: accept, work, delegate (`actions_tasks.py`), done/block (`actions_lifecycle.py`, checked by tool_evidence.py and shared_handoff.py)
    4.  `/core` agent_loop/task_origin_mirrors.py: status one-liners into the origin thread → `/ui` Tasks place and desk refresh via operator-invalidate
*   **Extension command (e.g. Browser Vision):**
    1.  `/ui` extensions/extensions-dialog.js → `/api` routes/extensions.py: enable (writes `extensions_enabled`) and run one-click setup (`core/extensions/setup_runner.py` → `/extensions` browser-vision/install.py)
    2.  `/core` extensions/registry.py + loader.py: discover manifests and import enabled packages in the worker; prompt_blocks.py adds the extension prompt to the working prompt
    3.  `/core` bm_cli/runtime.py → extensions/cli_bridge.py → `/extensions` browser-vision/commands.py (`bv ...`) → browser_host.py (Playwright) → screenshot + marks returned as CLI result
    4.  `/api` websocket.py extension live nudge → `/ui` extensions/browser-vision-status.js → extensions-live.js viewer
*   **Telegram operator:**
    1.  `/integrations` telegram/bot.py: allowlist check (auth.py), then commands or plain text → `/core` messaging.py (same ingress as the UI)
    2.  `/core` runtime/services.py `_dispatch_event` → `/integrations` telegram/bridge.py: push replies, channel messages and approval cards to session holders

## 5. Scope Map

- 📁 **/api** - FastAPI transport: `/api` REST routers, `/api/ws` WebSocket, local token auth, secret redaction · `fastapi, rest, websocket, auth` · 24 files · [detail](memory-bank/api.md)
  - 📁 **routes/** - `/api`-prefixed routers, one module per HTTP group plus private `_*` helpers
- 📁 **/core** - Domain and runtime layer: agent turn loop, CLI sandbox, LLM, tasks, floors, world, extension host · `agent-loop, runtime, cli, llm, extensions` · 199 files · [detail](memory-bank/core.md)
  - 📁 **agent_loop/** - Agent turn engine: dispatcher, decision/execution turns, channel/meeting rounds, notifications
  - 📁 **agent_pack/** - Agent pack (hire contract) schema, catalog, GitHub import/export
  - 📁 **bm_cli/** - Controlled virtual CLI for agents: commands, policy, consent gates, shell sandbox, git
    - 📁 **managed_writer/** - LLM-managed file/section authoring behind the CLI
  - 📁 **extensions/** - Extension host: discovery, manifest, loader, CLI bridge, prompt blocks
  - 📁 **llm/** - LLM client, routing, context assembly, call budget, templating
  - 📁 **models/** - Pydantic domain and API models
  - 📁 **prompting/** - Model-facing prompt surface registry and lint
  - 📁 **runtime/** - App/runtime-worker process split and event bridge
  - 📁 **tasking/** - Task service, board views, status transitions
  - 📁 **world/** - Office tilemap, pathfinding, movement simulation
- 📁 **/db** - SQLite storage layer: schema, connection/migrations, hardened CRUD helpers, per-domain repositories behind a `db.<fn>` façade · `sqlite, persistence, migrations, secrets` · 52 files · [detail](memory-bank/db.md)
- 📁 **/desktop** - Tauri v2 native shell: backend spawn/teardown, webview, Needs tray badge, external URL guard, clipboard image read · `rust, tauri, desktop` · 8 files · [detail](memory-bank/desktop.md)
  - 📁 **icons/** - App/bundle icons (png, icns, ico); `icon.svg` is the vector source
  - 📁 **gen/schemas/** - Tauri-generated ACL/capability JSON schemas (generated; not indexed)
  - 📁 **src/** - Rust sources for the shell binary
- 📁 **/extensions** - Optional manifest-driven agent capabilities loaded by the extension host when enabled · `extensions, plugins, playwright, microsoft-graph` · 23 files · [detail](memory-bank/extensions.md)
  - 📁 **browser-vision/** - `bv` command: screenshot-based web browsing with marks, pointer and keypad zoom; paced per site with bot-check cooldowns
  - 📁 **ms365-mail/** - `mail` command: per-agent Microsoft 365 mailbox via Microsoft Graph app-only auth
- 📁 **/integrations** - External service integrations; currently the Telegram bot · `telegram, bot, integrations` · 8 files · [detail](memory-bank/integrations.md)
  - 📁 **telegram/** - Telegram bot: commands, event bridge, session routing, allowlist auth
- 📁 **/plugins** - Empty placeholder (`.gitkeep` only); nothing implemented · `placeholder` · 1 file · [detail](memory-bank/plugins.md)
- 📁 **/prompts** - Default Markdown prompt templates loaded by `core/default_prompts.py` into settings · `prompts, templates, personalities` · 56 files · [detail](memory-bank/prompts.md)
  - 📁 **internal/** - Runtime-injected follow-up, repair, CLI-result and managed-writer snippets (keys prefixed `internal_`)
  - 📁 **personalities/** - Seeded role personality templates with decision/execution-conditional guidance
- 📁 **/scripts** - Manual dev/QA utilities: smoke suites, desktop icon generation, peer-assign scenario runner · `scripts, smoke-test, tooling` · 4 files · [detail](memory-bank/scripts.md)
- 📁 **/tests** - Pytest suite plus Node `.cjs` UI harnesses over a shared fake DOM · `pytest, js-harness, testing` · 263 files · [detail](memory-bank/tests.md)
  - 📁 **fixtures/** - Static test fixtures
    - 📁 **agent_mp/** - Fake agent-pack marketplace (catalog + packs) for pack/template/contract tests
- 📁 **/ui** - Operator frontend: one Jinja page plus build-less classic-script JS modules and token-driven CSS · `frontend, vanilla-js, css, websocket` · 216 files · [detail](memory-bank/ui.md)
  - 📁 **templates/** - Server-rendered page shell
  - 📁 **static/** - Static assets
    - 📁 **css/** - Token-driven stylesheets (no hex outside tokens.css); `css/vendor/` vendored, not indexed
    - 📁 **js/** - Classic-script modules exposing `window.BossMod*` globals (no build step, no ES imports)
