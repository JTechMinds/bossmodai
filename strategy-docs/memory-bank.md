---
schema_version: 2.0
last_updated_utc: 2026-03-21T19:30:00Z
processed_scopes:
  - directory: "/api"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
  - directory: "/core"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
  - directory: "/db"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
  - directory: "/ui"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
  - directory: "/desktop"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
  - directory: "/"
    commit_hash: "3708a6e7b2cbbb8093c8d31102dc5711c23a6d05"
---

# Project Memory Bank: BossMod AI

## 1. Project Summary

BossMod AI is a self-hosted platform for orchestrating autonomous AI agent teams within a visual 2D office environment. Agents occupy desks on a tile-based office map, navigate via A* pathfinding, socially interact when nearby, and execute work tasks through multi-turn LLM loops. The system supports multi-provider LLM routing (per agent, per activation mode), real-time WebSocket broadcasting, a Guardian safety system, and full diagnostic tracing of every agent turn. A Tauri desktop shell wraps the web UI for standalone distribution.

## 2. Technology Stack

*   **Backend:** Python 3.12+, FastAPI 0.115+, uvicorn, Jinja2, asyncio
*   **Database:** DuckDB 1.2+ (embedded, single-file `bossmod.db`)
*   **LLM Integration:** litellm 1.60+ (multi-provider: OpenAI, Anthropic, Ollama, etc.)
*   **Data Validation:** Pydantic 2.10+
*   **HTTP Client:** httpx 0.28+
*   **Pathfinding:** pathfinding 1.0+ (A* grid-based)
*   **Frontend:** Vanilla JavaScript (IIFE module pattern), Tailwind CSS (CDN), Lucide Icons (CDN), Split.js (CDN)
*   **Desktop:** Tauri 2 (Rust), ureq, serde
*   **Dev Tools:** uv (package manager), pytest, pytest-asyncio

## 3. Core Concepts & Data Models

*   **Agent:** Persistent identity with name, role, prompt template, color, per-mode LLM model overrides (social/work/reasoning/extraction/self_queue), API credentials, desk coordinates, and Guardian safety thresholds. Lives in `agents` table.
*   **AgentState:** Runtime position (x, y), status (idle/work_active/social_active/in_transit), timing (last_active_at, idle_since), and current_task_id. One-to-one with Agent. Lives in `agent_state` table.
*   **Message:** Communication event between agents, from humans, or system-generated. Has sender/recipient, content, type (work/social/human/system/meeting), spatial context (location_x/y), and token count.
*   **Task:** Unit of work with title, description, project, assignee, creator, status lifecycle (pending → active → complete/blocked/delegated/abandoned), parent-child nesting, and cost ceiling.
*   **AIConnection:** Saved LLM provider configuration (base URL, API key, model name, extra request body). Reusable across agents.
*   **AIPersonality:** Reusable prompt template defining agent behavior. Assigned to agents via the UI.
*   **Setting:** Key-value application configuration entry with category. 62+ seed defaults across simulation, social, llm, context, and advanced categories. All runtime config reads from this table.
*   **Diagnostic:** Full trace of a single agent turn — trigger, context sent, raw LLM response, parsed action, execution result, token counts, duration, errors. Auto-purges when exceeding retention limit.
*   **MemoryNode:** Entity-attribute-value knowledge graph entry (schema exists, not yet actively used).
*   **Room:** Spatial region on the office tilemap with type (workspace/meeting/break/hallway) and bounds.

## 4. Primary User/Data Flows

*   **Agent Turn (Simulation-Triggered):**
    1.  `WorldSimulation._tick()` checks activation triggers for idle agents via `check_activation()`.
    2.  Trigger found (social proximity, unread message, or pending task).
    3.  `run_turn(agent, state, trigger)` orchestrates multi-turn loop.
    4.  Mode determined (social/work), model selected via `select_model_with_source()`.
    5.  Context built: system prompt template + personality + world status + message window + task.
    6.  LLM called via `litellm.acompletion()`, response parsed as flat JSON action.
    7.  Action executed (work/message/walkTo/idle/complete/blocked/etc.).
    8.  Guardian checks applied (token explosion, velocity burst, repetition, no-progress).
    9.  Non-terminal actions loop back to step 6; terminal actions end the turn.
    10. Diagnostic row written, agent returns to idle, results broadcast via WebSocket.

*   **Agent Turn (Human-Triggered via Chat):**
    1.  User sends message in Chat sub-view → `POST /api/agents/{id}/activate` with content.
    2.  Human message persisted, broadcast via WebSocket.
    3.  Agent state set to work_active, `run_turn()` launched as background task.
    4.  Same flow as simulation-triggered turn from step 4 onward.

*   **Agent Creation:**
    1.  User opens agent form via "New Agent" button or Edit sub-view.
    2.  `AgentPanel.renderInline()` renders form with personality/connection dropdowns.
    3.  On save: personality prompt copied, connection credentials resolved, `POST /api/agents`.
    4.  Backend creates agent + companion state row, broadcasts world update + activity.

*   **Settings Management:**
    1.  User opens Settings view (full-screen overlay replacing main layout).
    2.  Six sections: Connections, Personalities, System Settings, Advanced, Prompt Template, Actions Schema.
    3.  Changes written via `PUT /api/settings/{key}`, config cache reloaded.
    4.  Connection test: `POST /api/connections/test` hits provider's `/models` endpoint.

*   **Real-Time Updates:**
    1.  WebSocket established on page load, receives initial world state + activity history.
    2.  Events routed by `app.js`: `world_update` → canvas, `activity` → log, `chat_message` → chat, `diagnostic` → diagnostics view.
    3.  Activity events persisted to `activity_log` table for history across restarts.

## 5. Codebase Index

- 📄 **main.py**
    - **Responsibility:** FastAPI application entry point; manages app lifespan (DB init, simulation start/stop), serves Jinja2 index template, mounts static files, health endpoint.
    - **Tags:** `entry-point, fastapi, lifecycle`
    - **Uses:** `api/routes.py, core/world/simulation.py, db/__init__.py`

- 📄 **run.sh**
    - **Responsibility:** Smart Tauri desktop launcher; syncs Python deps, conditionally rebuilds Rust binary, launches desktop app.
    - **Tags:** `launcher, build, desktop`
    - **Uses:** `desktop/`

- 📄 **pyproject.toml**
    - **Responsibility:** Python project metadata, dependency declarations, and dev tooling configuration.
    - **Tags:** `config, dependencies, build`
    - **Uses:** `n/a`

- 📁 **api/** — REST API layer and real-time WebSocket communication.
    - 📄 **routes.py**
        - **Responsibility:** Central FastAPI router defining all HTTP endpoints — agents CRUD, tasks CRUD, world state, settings, AI connections, AI personalities, diagnostics, agent activation with background turn execution.
        - **Tags:** `api, routing, rest, websocket, crud`
        - **Uses:** `api/websocket.py, core/agent_loop/loop.py, core/llm/client.py, core/models/*, core/world/simulation.py, core/world/tilemap.py, db/*`
    - 📄 **websocket.py**
        - **Responsibility:** WebSocket connection manager; tracks active connections, broadcasts world state, activity events, chat messages, and diagnostics to all clients; persists activity to database.
        - **Tags:** `websocket, realtime, broadcast, events`
        - **Uses:** `core/config.py, db/*`

- 📁 **core/** — Business logic: agent orchestration, LLM integration, world simulation.
    - 📄 **config.py**
        - **Responsibility:** Centralized settings reader with lazy-load cache; typed accessors (string/int/float) with required/optional variants; all runtime config flows through this module.
        - **Tags:** `config, settings, cache`
        - **Uses:** `db/*`
    - 📁 **agent_loop/** — Multi-turn agent activation, action execution, and safety.
        - 📄 **loop.py**
            - **Responsibility:** Orchestrates the complete multi-turn agent activation loop — determines mode, selects model, builds context, calls LLM in a loop, parses/executes actions, applies Guardian checks, writes diagnostics at every exit path, broadcasts results.
            - **Tags:** `orchestration, agent-loop, multi-turn, diagnostics`
            - **Uses:** `api/websocket.py, core/agent_loop/actions.py, core/agent_loop/guardian.py, core/llm/*, core/models/*, db/*`
        - 📄 **actions.py**
            - **Responsibility:** Parses flat JSON actions from LLM responses and dispatches to handler functions; enforces workspace rules (must be at desk to work); manages agent locomotion via pathfinding.
            - **Tags:** `action-parsing, execution, pathfinding`
            - **Uses:** `core/llm/client.py, core/models/*, core/world/pathfinding.py, core/world/tilemap.py, db/*`
        - 📄 **activation.py**
            - **Responsibility:** Determines when an idle agent should activate; implements 4-gate social trigger (proximity, idle time, cooldown, nearby agent) plus immediate work triggers (messages, tasks).
            - **Tags:** `activation, triggers, social, scheduling`
            - **Uses:** `core/config.py, core/models/*, db/*`
        - 📄 **guardian.py**
            - **Responsibility:** Zero-API-cost pathological behavior detection; four safety rules: token explosion, velocity burst, repetition (Jaccard similarity), and no-progress detection.
            - **Tags:** `safety, guardian, rate-limiting, anomaly-detection`
            - **Uses:** `core/models/*, core/llm/client.py, db/*`
    - 📁 **llm/** — LLM client abstraction, context assembly, and model routing.
        - 📄 **client.py**
            - **Responsibility:** Unified async LLM client via litellm; abstracts provider differences, manages concurrency semaphore, provides token counting, handles provider-specific body parameters.
            - **Tags:** `llm, client, litellm, async, concurrency`
            - **Uses:** `core/config.py`
        - 📄 **context_builder.py**
            - **Responsibility:** Builds the full LLM message list by resolving template variables (personality, agent name, role, memory, world status, task, available actions) from settings at runtime.
            - **Tags:** `context, prompt-building, templates`
            - **Uses:** `core/config.py, core/models/*, core/world/tilemap.py`
        - 📄 **routing.py**
            - **Responsibility:** Model selection matrix; resolves appropriate model per agent and activation mode (social/work/reasoning/extraction/self_queue); agent override → global setting → None.
            - **Tags:** `routing, model-selection, config`
            - **Uses:** `core/config.py, core/models/*`
    - 📁 **models/** — Pydantic domain models for all entities.
        - 📄 **__init__.py**
            - **Responsibility:** Central re-export hub for all Pydantic domain models.
            - **Tags:** `models, exports`
            - **Uses:** `core/models/agent.py, core/models/memory.py, core/models/message.py, core/models/settings.py, core/models/task.py`
        - 📄 **agent.py**
            - **Responsibility:** Defines Agent (persistent identity/configuration), AgentState (runtime position/activity), and API input models; includes per-agent LLM model overrides and Guardian thresholds.
            - **Tags:** `model, agent, pydantic`
            - **Uses:** `n/a`
        - 📄 **message.py**
            - **Responsibility:** Defines Message model for inter-agent and system communication; includes HUMAN_SENDER_ID sentinel constant.
            - **Tags:** `model, message, pydantic`
            - **Uses:** `n/a`
        - 📄 **task.py**
            - **Responsibility:** Defines Task model with status lifecycle and parent-child nesting support.
            - **Tags:** `model, task, pydantic`
            - **Uses:** `n/a`
        - 📄 **memory.py**
            - **Responsibility:** Defines Setting model for system-wide configuration entries.
            - **Tags:** `model, setting, pydantic`
            - **Uses:** `n/a`
        - 📄 **settings.py**
            - **Responsibility:** Defines AIConnection and AIPersonality models with CRUD input/output variants.
            - **Tags:** `model, ai-connection, ai-personality, pydantic`
            - **Uses:** `n/a`
    - 📁 **world/** — Office environment, spatial logic, and simulation.
        - 📄 **simulation.py**
            - **Responsibility:** Background asyncio simulation loop; every tick advances in-transit agents along paths, checks activation triggers for idle agents, and launches agent turns concurrently.
            - **Tags:** `simulation, async, background-task, movement`
            - **Uses:** `api/websocket.py, core/config.py, core/agent_loop/activation.py, core/agent_loop/loop.py, core/world/tilemap.py, db/*`
        - 📄 **tilemap.py**
            - **Responsibility:** Office tilemap definition (28×20 grid) with tile types, room metadata, desk positions; provides map data serialization for frontend and location-based rule enforcement.
            - **Tags:** `tilemap, spatial, rooms, desks, map`
            - **Uses:** `n/a`
        - 📄 **pathfinding.py**
            - **Responsibility:** A* pathfinding on the office tilemap; agents use this to navigate between desks, meeting rooms, and break rooms.
            - **Tags:** `pathfinding, a-star, navigation`
            - **Uses:** `core/world/tilemap.py`

- 📁 **db/** — Modular database layer with hardened CRUD helpers.
    - 📄 **__init__.py**
        - **Responsibility:** Barrel export re-exporting all public database functions; consumers import via `import db` and call `db.create_agent()`, etc.
        - **Tags:** `exports, barrel`
        - **Uses:** `db/connection.py, db/crud.py, db/agents.py, db/messages.py, db/tasks.py, db/settings.py, db/activity.py, db/ai_connections.py, db/ai_personalities.py, db/diagnostics.py, db/world.py`
    - 📄 **connection.py**
        - **Responsibility:** DuckDB singleton connection lifecycle; schema initialization from `schema.sql` on first access; soft migrations for column additions; seeds defaults on init.
        - **Tags:** `connection, schema, migration, duckdb`
        - **Uses:** `db/schema.sql, db/settings.py, db/ai_personalities.py`
    - 📄 **schema.sql**
        - **Responsibility:** Complete DuckDB DDL defining 16 tables with CHECK constraints, UUID defaults, and timestamp defaults. Safe to execute on every startup.
        - **Tags:** `schema, ddl, tables`
        - **Uses:** `n/a`
    - 📄 **crud.py**
        - **Responsibility:** Hardened reusable CRUD helpers centralizing all database access — parameterized queries, row-to-dict conversion, Pydantic model validation, column-whitelisted updates.
        - **Tags:** `crud, helpers, parameterized, security`
        - **Uses:** `db/connection.py`
    - 📄 **agents.py**
        - **Responsibility:** Agent and AgentState CRUD; creates companion state row on agent creation; auto-timestamps idle/active transitions.
        - **Tags:** `crud, agents, state`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **messages.py**
        - **Responsibility:** Message CRUD with sender name resolution; batch agent lookups for formatted message history.
        - **Tags:** `crud, messages, formatting`
        - **Uses:** `core/models/*, db/crud.py, db/agents.py`
    - 📄 **tasks.py**
        - **Responsibility:** Task CRUD with status lifecycle tracking and auto-timestamping on status changes.
        - **Tags:** `crud, tasks, status`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **settings.py**
        - **Responsibility:** Settings CRUD and 62+ seed defaults across 7 categories; includes system prompt template and available actions schema.
        - **Tags:** `crud, settings, seed, config`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **activity.py**
        - **Responsibility:** Activity log CRUD; records persistent event history for UI activity feed.
        - **Tags:** `crud, activity, events`
        - **Uses:** `db/crud.py`
    - 📄 **ai_connections.py**
        - **Responsibility:** AI Connection CRUD for saved LLM provider configurations.
        - **Tags:** `crud, ai-connections, llm-config`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **ai_personalities.py**
        - **Responsibility:** AI Personality CRUD with default personality seeding.
        - **Tags:** `crud, ai-personalities, prompts`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **diagnostics.py**
        - **Responsibility:** Diagnostics CRUD with auto-retention purging; records full trace data per agent turn.
        - **Tags:** `crud, diagnostics, trace, auto-purge`
        - **Uses:** `core/config.py, db/crud.py`
    - 📄 **world.py**
        - **Responsibility:** World state assembly (agent + state JOIN) and spatial proximity queries (Manhattan distance).
        - **Tags:** `crud, world, spatial`
        - **Uses:** `db/crud.py`

- 📁 **ui/** — Server-rendered SPA frontend.
    - 📁 **templates/**
        - 📄 **index.html**
            - **Responsibility:** Jinja2 root template; defines DOM structure with nav bar, resizable panels (Split.js), canvas container, sub-view containers (Chat, Edit, Tasks, Diagnostics), settings overlay, and mobile bottom sheet. Loads all CDN deps and JS modules.
            - **Tags:** `template, html, layout, jinja2`
            - **Uses:** `ui/static/css/style.css, ui/static/js/*.js`
    - 📁 **static/css/**
        - 📄 **style.css**
            - **Responsibility:** Custom component styles extending Tailwind — tab states, Split.js gutters, chat bubbles, diagnostic cards, activity entries, mobile bottom sheet, scrollbar styling.
            - **Tags:** `css, styling, components`
            - **Uses:** `n/a`
    - 📁 **static/js/**
        - 📄 **app.js**
            - **Responsibility:** Main application controller; bootstraps UI, manages WebSocket connection with auto-reconnect, routes WebSocket events to modules, handles top-level navigation and Split.js panel persistence.
            - **Tags:** `controller, bootstrap, websocket, routing`
            - **Uses:** `ui/static/js/utils.js, ui/static/js/canvas.js, ui/static/js/activity.js, ui/static/js/agent-context.js, ui/static/js/diagnostics.js, ui/static/js/settings-view.js`
        - 📄 **agent-context.js**
            - **Responsibility:** Manages selected agent state and left panel UI; handles agent selection/deselection, tab switching between sub-views (Chat, Edit, Tasks, Diagnostics), chat message sending and display, task list rendering.
            - **Tags:** `controller, agent-selection, chat, subviews`
            - **Uses:** `ui/static/js/utils.js, ui/static/js/agent-panel.js, ui/static/js/diagnostics.js`
        - 📄 **agent-panel.js**
            - **Responsibility:** Agent creation/editing form; handles personality/connection resolution, color/desk selection, CRUD operations, and form data transformation for API submission.
            - **Tags:** `controller, form, agent-crud, ui`
            - **Uses:** `ui/static/js/utils.js, ui/static/js/settings-view.js`
        - 📄 **canvas.js**
            - **Responsibility:** 2D canvas office map renderer; draws tilemap, room labels, desk monitors, and agent circles with status indicators; handles click-to-select and hover effects.
            - **Tags:** `canvas, rendering, tilemap, interaction`
            - **Uses:** `ui/static/js/utils.js, ui/static/js/app.js`
        - 📄 **activity.js**
            - **Responsibility:** Activity log display; receives WebSocket events, renders with event-specific icons and color-coded badges, maintains max 100 entries.
            - **Tags:** `controller, activity-log, events`
            - **Uses:** `ui/static/js/utils.js`
        - 📄 **diagnostics.js**
            - **Responsibility:** Diagnostics tab controller; renders compact summary cards per agent turn, lazy-loads full trace details on expand, handles WebSocket diagnostic events.
            - **Tags:** `controller, diagnostics, trace-viewer`
            - **Uses:** `ui/static/js/utils.js`
        - 📄 **settings-view.js**
            - **Responsibility:** Full-screen settings view with 6 sections — AI Connections (CRUD + test), AI Personalities (CRUD), System Settings, Advanced (diagnostics toggle, retention), System Prompt Template, Actions Schema.
            - **Tags:** `controller, settings, connections, personalities, config-ui`
            - **Uses:** `ui/static/js/utils.js, ui/static/js/app.js`
        - 📄 **utils.js**
            - **Responsibility:** Shared utility functions — HTML escaping (XSS prevention), agent data normalization, status color/class mappings, overlay panel animations.
            - **Tags:** `utilities, helpers, xss-prevention`
            - **Uses:** `n/a`

- 📁 **desktop/** — Tauri desktop application shell.
    - 📄 **Cargo.toml**
        - **Responsibility:** Rust package manifest for the Tauri desktop wrapper.
        - **Tags:** `config, rust, tauri`
        - **Uses:** `n/a`
    - 📄 **tauri.conf.json**
        - **Responsibility:** Tauri window configuration (1280×800, min 900×600); dev/prod URL pointing to FastAPI backend at 127.0.0.1:8000.
        - **Tags:** `config, tauri, window`
        - **Uses:** `n/a`
    - 📁 **src/**
        - 📄 **main.rs**
            - **Responsibility:** Tauri application entry point; launches desktop window wrapping the web UI.
            - **Tags:** `entry-point, tauri, rust`
            - **Uses:** `n/a`

- 📁 **docs/** — Design specifications and implementation plans.
    - 📁 **superpowers/specs/**
        - 📄 **2026-03-21-diagnostics-design.md**
            - **Responsibility:** Full design specification for the diagnostics feature — data model, settings, capture points, frontend UI, verification checklist.
            - **Tags:** `spec, design, diagnostics`
            - **Uses:** `n/a`
    - 📁 **superpowers/plans/**
        - 📄 **2026-03-21-diagnostics.md**
            - **Responsibility:** Step-by-step implementation plan for diagnostics feature with code snippets and task breakdown.
            - **Tags:** `plan, implementation, diagnostics`
            - **Uses:** `n/a`

- 📁 **strategy-docs/** — Vision documents and enhancement proposals.
    - 📄 **BossMod_AI_Vision.docx**
        - **Responsibility:** High-level project vision and roadmap.
        - **Tags:** `vision, roadmap, strategy`
        - **Uses:** `n/a`
    - 📄 **BossMod_Enhancement_v1.docx**
        - **Responsibility:** Enhancement proposals for future development.
        - **Tags:** `enhancements, proposals, strategy`
        - **Uses:** `n/a`
    - 📄 **pixel_generator_prompt.md**
        - **Responsibility:** Sprite sheet generation prompt for character pixel art assets.
        - **Tags:** `assets, sprites, prompt`
        - **Uses:** `n/a`

- 📁 **tests/** — Test suite (placeholder, not yet populated).
    - 📄 **__init__.py**
        - **Responsibility:** Test package initialization (empty).
        - **Tags:** `tests, placeholder`
        - **Uses:** `n/a`

- 📁 **artifacts/** — Sample agent configurations and project templates.
- 📁 **plugins/** — Plugin directory (empty, reserved for future use).
