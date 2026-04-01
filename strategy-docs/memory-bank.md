---
schema_version: 2.0
last_updated_utc: 2026-04-01T12:00:00Z
processed_scopes:
  - directory: "/"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/api"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/core"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/db"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/ui"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/desktop"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/prompts"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/integrations"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/tests"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
  - directory: "/scripts"
    commit_hash: "ea4794db9d24200b41c8e51322fc809c34cd75c6"
---

# Project Memory Bank: BossMod AI

## 1. Project Summary

BossMod AI is a self-hosted platform for orchestrating autonomous AI agent teams within a visual 2D office environment. Agents occupy desks on a tile-based office map, navigate via A* pathfinding, socially interact when nearby, and execute work tasks through multi-turn LLM loops governed by strict JSON contracts (decision and execution). The system features a durable trigger-driven dispatch architecture, a full virtual CLI for agent filesystem/git operations, multi-provider LLM routing, real-time WebSocket broadcasting, a Guardian safety system, structured task management with work contracts, and comprehensive diagnostic tracing. A Tauri desktop shell wraps the web UI for standalone offline distribution.

## 2. Technology Stack

*   **Backend:** Python 3.12+, FastAPI 0.115+, uvicorn, Jinja2, asyncio
*   **Database:** SQLite 3 (embedded, thread-local connection pooling, schema migrations)
*   **LLM Integration:** litellm 1.60+ (multi-provider: OpenAI, Anthropic, Ollama, etc.)
*   **Data Validation:** Pydantic 2.10+
*   **HTTP Client:** httpx 0.28+
*   **Pathfinding:** pathfinding 1.0+ (A* grid-based)
*   **Frontend:** Vanilla JavaScript (IIFE module pattern), Tailwind CSS (CDN), Lucide Icons (CDN), Split.js (CDN), highlight.js (bundled vendor), marked.js (bundled vendor)
*   **Desktop:** Tauri 2 (Rust), ureq, serde
*   **Integrations:** Telegram bot (python-telegram-bot)
*   **Dev Tools:** uv (package manager), pytest, pytest-asyncio

## 3. Core Concepts & Data Models

*   **Agent:** Persistent identity with name, role, prompt template, color, per-mode LLM model overrides (social/work/reasoning/extraction/self_queue), API credentials, desk coordinates, and Guardian safety thresholds.
*   **AgentState:** Runtime position (x, y), status (idle/waiting/blocked/work_active/social_active/in_transit), timing, and current_task_id.
*   **AgentTrigger:** Durable wake-up event queued for an agent — types include human_chat, cli_approval_resolved, peer_message, task_assigned, task_follow_up, activity_resumed, social, session_message, channel_message, watchdog_status_ping. Priority-ordered dispatch.
*   **Activity:** Durable runtime state representing an agent's current commitment — types: work, meeting, conversation, movement, break, social. Parent-child chains for resumable workflows.
*   **Task:** Unit of work with title, description, project, assignee, creator, status lifecycle (pending → accepted → active → waiting → blocked → complete/stalled/abandoned/delegated/declined), parent-child nesting, work contracts, notification policies, and cost ceiling.
*   **TaskEvent:** Durable task-thread audit entry — event types: comment, clarification, answer, status_update, blocker, completion, assignment, reprioritized, system. Author types: human, agent, system.
*   **WorkContract:** Structured deliverable specification attached to a task — defines expected output files with virtual paths.
*   **Message:** Communication event between agents, from humans, or system-generated. Has sender/recipient, content, type (work/social/human/system/meeting), spatial context, and token count.
*   **AIConnection:** Saved LLM provider configuration (base URL, API key, model name, extra request body). Reusable across agents.
*   **AIPersonality:** Reusable prompt template defining agent behavior. 10 shipped defaults (software_engineer, project_manager, research_analyst, etc.).
*   **Setting:** Key-value application configuration entry with category. 62+ seed defaults across simulation, social, llm, context, desk, and advanced categories.
*   **Diagnostic:** Full trace of a single agent turn — trigger, context sent, raw LLM response, parsed action, execution result, token counts, duration, errors. Auto-purges when exceeding retention limit.
*   **Notification:** First-class human-facing event projected from task completion, blockers, or delegation. Links to desk UI via notification_links.
*   **Channel:** Manual communication channel for team conversations with response round orchestration.
*   **MeetingSession:** Meeting room orchestration with participant response rounds.
*   **Room:** Spatial region on the office tilemap with type (workspace/meeting/break/hallway) and bounds.

## 4. Primary User/Data Flows

*   **Agent Turn (Trigger-Dispatched):**
    1.  `dispatcher.py` polls the `agent_triggers` queue for the next highest-priority trigger.
    2.  Trigger atomically claimed; `activity_scheduler.py` prepares runtime context (activities, work states).
    3.  `loop.py` routes to either Decision Runtime or Execution path based on trigger type.
    4.  **Decision turn:** `decision_runtime.py` materializes agent's response into a commitment (accept, reply, clarify, decline, defer, cancel).
    5.  **Execution turn:** `actions.py` parses flat JSON actions (cli, work, msg, taskmsg, assign, walk, mtg, idle, wait, done, block, deleg, drop) and dispatches to handlers.
    6.  Context built by `context_builder.py`: system prompt + personality + world status + activity + task + task board + team directory + communication snapshot.
    7.  LLM called via `litellm.acompletion()`, response validated against JSON contract.
    8.  Guardian checks applied (token explosion, velocity burst, repetition, no-progress).
    9.  `liveness.py` records heartbeat/progress for task monitoring.
    10. `notifications.py` projects outcomes to human-facing UI; diagnostic row written; results broadcast via WebSocket.

*   **Agent Turn (Human-Triggered via Chat):**
    1.  User sends message in Chat sub-view → `POST /api/agents/{id}/activate` with content.
    2.  Human message persisted, `human_chat` trigger queued in `agent_triggers`.
    3.  Dispatcher picks up trigger, routes through Decision Runtime for initial response.
    4.  If agent accepts work, an Activity is created and execution turns follow.

*   **BossMod CLI Execution (within Agent Turn):**
    1.  Agent emits `{"act":"cli","data":{"cmd":"bwrite path/to/file"}}` action.
    2.  `bm_cli/runtime.py` dispatches to appropriate handler (fs, git, state, help).
    3.  `policy_engine.py` evaluates command against CLI policy rules; may pause for human approval.
    4.  `virtual_fs.py` provides agent-isolated storage; `managed_writer.py` handles multi-pass file writes.
    5.  `audit.py` logs the operation; `artifacts.py` registers file outputs.
    6.  Result returned to agent's next LLM turn as context.

*   **Task Lifecycle:**
    1.  Task created via API or agent delegation (`assign` action).
    2.  `task_assigned` trigger queued for assignee agent.
    3.  Agent accepts → task status: accepted → active. Work contract binds deliverables.
    4.  Progress tracked via `liveness.py` heartbeats and `task_events` thread.
    5.  Completion: agent emits `done` with summary; `resolution.py` validates deliverables against contract.
    6.  Notification projected to requester; task status: complete.

*   **Settings Management:**
    1.  User opens Settings view (full-screen overlay with 2-column nav).
    2.  Sections: AI Connections, AI Personalities, System Settings, CLI Policy, Telegram, Advanced, System Prompt Template, Runtime Contracts.
    3.  Changes written via `PUT /api/settings/{key}`, config cache reloaded.
    4.  Connection test: `POST /api/connections/test` hits provider's `/models` endpoint.

*   **Real-Time Updates:**
    1.  WebSocket established on page load, receives initial world state + activity history.
    2.  Events routed by `app.js`: `world_update` → canvas, `activity` → log, `chat_message` → chat, `diagnostic` → diagnostics view, `task_update` → company tasks.
    3.  Activity events persisted to `activity_log` table for history across restarts.

## 5. Codebase Index

- 📄 **main.py**
    - **Responsibility:** FastAPI application entry point; manages app lifespan (DB init, simulation start/stop), serves Jinja2 index template, mounts static files, health endpoint.
    - **Tags:** `entry-point, fastapi, lifecycle`
    - **Uses:** `api/routes.py, core/world/simulation.py, db/*`

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
        - **Responsibility:** Central FastAPI router defining all HTTP endpoints — agents CRUD, tasks CRUD, world state, settings, AI connections/personalities, diagnostics, agent activation, company files CRUD, CLI policy, Telegram config, runtime control.
        - **Tags:** `api, routing, rest, websocket, crud`
        - **Uses:** `api/websocket.py, core/agent_loop/*, core/llm/client.py, core/models/*, core/world/*, db/*`
    - 📄 **websocket.py**
        - **Responsibility:** WebSocket connection manager; tracks active connections, broadcasts world state, activity events, chat messages, diagnostics, and task updates to all clients; persists activity to database.
        - **Tags:** `websocket, realtime, broadcast, events`
        - **Uses:** `core/config.py, db/*`

- 📁 **core/** — Business logic: agent orchestration, LLM integration, CLI, task management, world simulation.
    - 📄 **config.py**
        - **Responsibility:** Centralized settings reader with lazy-load cache; typed accessors (string/int/float) with required/optional variants; all runtime config flows through this module.
        - **Tags:** `config, settings, cache`
        - **Uses:** `db/*`
    - 📄 **default_prompts.py**
        - **Responsibility:** Default prompt template loader from markdown files in `prompts/`; provides cached access to system prompt, contracts, personalities, and internal guidance templates.
        - **Tags:** `prompts, templates, loader, cache`
        - **Uses:** `prompts/*`
    - 📄 **file_explorer.py**
        - **Responsibility:** Cross-platform file manager launcher (Linux xdg-open, macOS open, Windows explorer) for opening agent workspace directories.
        - **Tags:** `utilities, file-system, cross-platform`
        - **Uses:** `n/a`
    - 📄 **messaging.py**
        - **Responsibility:** Messaging interfaces and domain models for inter-module communication.
        - **Tags:** `messaging, interfaces`
        - **Uses:** `n/a`
    - 📄 **time.py**
        - **Responsibility:** Time utilities and timezone handling for consistent timestamp generation.
        - **Tags:** `utilities, time`
        - **Uses:** `n/a`
    - 📁 **agent_loop/** — Trigger-driven agent turn execution engine (24 files).
        - 📄 **dispatcher.py**
            - **Responsibility:** Durable trigger dispatcher; polls the agent_triggers queue, atomically claims triggers, launches agent turns concurrently with activity scheduling.
            - **Tags:** `dispatcher, trigger-queue, async, concurrency`
            - **Uses:** `core/agent_loop/activity_runtime.py, core/agent_loop/activity_scheduler.py, core/agent_loop/loop.py, core/agent_loop/policies.py, db/*`
        - 📄 **loop.py**
            - **Responsibility:** Turn router; determines decision vs execution path, builds context, calls LLM in a loop, parses/executes actions, applies Guardian checks, writes diagnostics.
            - **Tags:** `orchestration, agent-loop, multi-turn, diagnostics`
            - **Uses:** `core/agent_loop/actions.py, core/agent_loop/decision_runtime.py, core/agent_loop/guardian.py, core/agent_loop/liveness.py, core/llm/*, db/*`
        - 📄 **activity_runtime.py**
            - **Responsibility:** Activity state transitions and commitment lifecycle management; creates/completes/cancels activities, refreshes agent status based on current commitments.
            - **Tags:** `runtime, state-management, activity-lifecycle`
            - **Uses:** `db/*, core/models/*`
        - 📄 **activity_scheduler.py**
            - **Responsibility:** Centralized trigger scheduling around runtime activities; prepares execution context and determines appropriate activity type per trigger.
            - **Tags:** `scheduling, triggers, context-preparation`
            - **Uses:** `core/agent_loop/activity_runtime.py, core/agent_loop/task_roles.py, core/models/*`
        - 📄 **decision_runtime.py**
            - **Responsibility:** Materializes direct-turn decisions (human chat, peer message, task assignment) into commitments via the decision contract.
            - **Tags:** `runtime, decisions, commitment-creation`
            - **Uses:** `core/agent_loop/activity_runtime.py, core/agent_loop/decision_contract.py`
        - 📄 **actions.py**
            - **Responsibility:** Parses flat JSON execution actions from LLM responses and dispatches to handler functions; enforces workspace rules (must be at desk to work); manages agent locomotion, CLI calls, messaging, task operations, and deliverables.
            - **Tags:** `action-parsing, execution, dispatch`
            - **Uses:** `core/agent_loop/activity_runtime.py, core/agent_loop/deliverables.py, core/agent_loop/task_roles.py, core/bm_cli/runtime.py, db/*`
        - 📄 **action_contract.py**
            - **Responsibility:** Execution contract prompt access with template variable rendering for the JSON action schema.
            - **Tags:** `contracts, prompts, execution-schema`
            - **Uses:** `core/config.py, core/llm/template_engine.py`
        - 📄 **decision_contract.py**
            - **Responsibility:** Unified conversation contract for decision parsing and validation; defines allowed actions per trigger type.
            - **Tags:** `contracts, decisions, validation, pydantic`
            - **Uses:** `core/config.py, core/bm_cli/contract.py`
        - 📄 **communication.py**
            - **Responsibility:** Shared communication snapshot builder; assembles deterministic context from messages, task events, and meeting/channel history for LLM turns.
            - **Tags:** `communication, context-snapshots, enrichment`
            - **Uses:** `db/*, core/tasking/*, core/models/*`
        - 📄 **prompt_history.py**
            - **Responsibility:** Backend-owned prompt-history view assembly; builds the LLM message list with token counting and truncation.
            - **Tags:** `context, history, token-management`
            - **Uses:** `db/*, core/llm/client.py, core/models/*`
        - 📄 **policies.py**
            - **Responsibility:** Trigger execution policy dataclass definitions; maps trigger types to per-type execution rules and constraints.
            - **Tags:** `policies, dataclasses, trigger-rules`
            - **Uses:** `n/a`
        - 📄 **turn_rules.py**
            - **Responsibility:** Context-aware validation rules for execution turns; enforces constraints like workspace requirement, active task requirement.
            - **Tags:** `validation, rules, context-guards`
            - **Uses:** `core/agent_loop/policies.py`
        - 📄 **liveness.py**
            - **Responsibility:** Task liveness bookkeeping; records heartbeats and progress for task monitoring and stall detection.
            - **Tags:** `monitoring, liveness, heartbeat`
            - **Uses:** `db/*, core/models/*`
        - 📄 **task_roles.py**
            - **Responsibility:** Durable task participant and reporting helpers; manages task ownership, assignment, and delegation chains.
            - **Tags:** `tasks, roles, delegation`
            - **Uses:** `db/*, core/models/*`
        - 📄 **task_origins.py**
            - **Responsibility:** Shared task origin mapping helpers; maps trigger types to originating communication channels.
            - **Tags:** `tasks, mapping, triggers`
            - **Uses:** `n/a`
        - 📄 **notifications.py**
            - **Responsibility:** First-class human-facing notification projection; converts turn outcomes into user-visible notifications with deep links.
            - **Tags:** `notifications, chat, ui-projection`
            - **Uses:** `db/*, core/models/*`
        - 📄 **message_delivery.py**
            - **Responsibility:** Shared message delivery semantics; distinguishes social vs work message routing and formatting.
            - **Tags:** `messaging, delivery, routing`
            - **Uses:** `core/models/*`
        - 📄 **deliverables.py**
            - **Responsibility:** Structured deliverable helpers for work activities; validates file outputs against work contract specifications.
            - **Tags:** `work-contracts, deliverables, validation`
            - **Uses:** `db/*, core/bm_cli/virtual_fs.py, core/models/*`
        - 📄 **guardian.py**
            - **Responsibility:** Zero-API-cost pathological behavior detection; four safety rules: token explosion, velocity burst, repetition (Jaccard similarity), and no-progress detection.
            - **Tags:** `safety, guardian, rate-limiting, anomaly-detection`
            - **Uses:** `core/models/*, core/llm/client.py, db/*`
        - 📄 **outcomes.py**
            - **Responsibility:** Turn outcome domain models; structured representations of what happened during a turn.
            - **Tags:** `models, outcomes`
            - **Uses:** `n/a`
        - 📄 **meeting_rounds.py**
            - **Responsibility:** Meeting/session round coordination; manages participant response ordering for meeting activities.
            - **Tags:** `communication, meetings, rounds`
            - **Uses:** `db/*`
        - 📄 **channel_rounds.py**
            - **Responsibility:** Channel thread round coordination; manages response ordering for channel conversations.
            - **Tags:** `communication, channels, rounds`
            - **Uses:** `db/*`
        - 📄 **watchdog.py**
            - **Responsibility:** Watchdog status ping handler; processes periodic health check triggers for agents.
            - **Tags:** `monitoring, watchdog, health`
            - **Uses:** `n/a`
    - 📁 **bm_cli/** — BossMod CLI: full interactive virtual terminal for agent operations (22 files).
        - 📄 **runtime.py**
            - **Responsibility:** CLI execution runtime; dispatches parsed commands to appropriate handler modules (fs, git, state, help) and manages execution flow.
            - **Tags:** `execution, dispatch, runtime`
            - **Uses:** `core/bm_cli/fs_commands.py, core/bm_cli/git_commands.py, core/bm_cli/state_commands.py, core/bm_cli/policy_engine.py, core/bm_cli/shell_executor.py`
        - 📄 **command_registry.py**
            - **Responsibility:** Read-only metadata registry for all built-in virtual commands; defines command names, descriptions, argument schemas.
            - **Tags:** `registry, metadata, commands`
            - **Uses:** `n/a`
        - 📄 **parser.py**
            - **Responsibility:** CLI command parser; tokenizes agent CLI input into command name and arguments.
            - **Tags:** `parsing, tokenization`
            - **Uses:** `n/a`
        - 📄 **fs_commands.py**
            - **Responsibility:** Filesystem-style virtual commands (pwd, cd, ls, cat, write, bwrite, append, mkdir, rm) operating on agent-isolated virtual storage.
            - **Tags:** `filesystem, commands, virtual-fs`
            - **Uses:** `core/bm_cli/virtual_fs.py, core/bm_cli/document_tools.py, core/bm_cli/workspace_git.py`
        - 📄 **git_commands.py**
            - **Responsibility:** Git integration commands for agent workspaces (status, commit, diff, log).
            - **Tags:** `git, vcs, commands`
            - **Uses:** `core/bm_cli/workspace_git.py`
        - 📄 **state_commands.py**
            - **Responsibility:** Runtime/state-oriented commands (status, runtime, activity, tasks) providing agents with introspection capabilities.
            - **Tags:** `state, introspection, commands`
            - **Uses:** `db/*, core/tasking/*, core/world/*`
        - 📄 **help_commands.py**
            - **Responsibility:** Help discovery and reference commands; lists available commands and their usage.
            - **Tags:** `help, discovery`
            - **Uses:** `core/bm_cli/command_registry.py`
        - 📄 **virtual_fs.py**
            - **Responsibility:** Virtual filesystem layer providing agent-isolated storage with path normalization and access boundaries.
            - **Tags:** `filesystem, isolation, virtual-storage`
            - **Uses:** `n/a`
        - 📄 **workspace_git.py**
            - **Responsibility:** Git integration for agent workspaces; runs git operations (status, commit, diff) within agent storage directories.
            - **Tags:** `git, workspace, subprocess`
            - **Uses:** `n/a`
        - 📄 **managed_writer.py**
            - **Responsibility:** Managed write system supporting single-pass, section planning, and rewrite strategies for large file creation.
            - **Tags:** `writers, managed-writes, multi-pass`
            - **Uses:** `core/bm_cli/virtual_fs.py`
        - 📄 **document_tools.py**
            - **Responsibility:** Markdown document parsing and manipulation utilities for structured content editing.
            - **Tags:** `documents, markdown, parsing`
            - **Uses:** `n/a`
        - 📄 **policy_engine.py**
            - **Responsibility:** Policy evaluation engine for CLI command approval; checks commands against whitelist/blacklist/approval tiers.
            - **Tags:** `policies, approval, security`
            - **Uses:** `db/cli_policy_rules.py`
        - 📄 **policies.py**
            - **Responsibility:** Policy rule definitions and checkers for CLI command classification.
            - **Tags:** `policies, rules`
            - **Uses:** `n/a`
        - 📄 **shell_executor.py**
            - **Responsibility:** Shell command execution via subprocess with sandboxing and timeout enforcement.
            - **Tags:** `shell, execution, subprocess`
            - **Uses:** `n/a`
        - 📄 **session.py**
            - **Responsibility:** CLI session state management (current working directory, permissions, context) per agent.
            - **Tags:** `session, state`
            - **Uses:** `db/*`
        - 📄 **contract.py**
            - **Responsibility:** CLI contract parsing; extracts and validates bm_cli action blocks from LLM output JSON.
            - **Tags:** `contracts, parsing, pydantic`
            - **Uses:** `n/a`
        - 📄 **results.py**
            - **Responsibility:** Result type definitions for CLI execution (success, error, needs_approval).
            - **Tags:** `results, types`
            - **Uses:** `n/a`
        - 📄 **artifacts.py**
            - **Responsibility:** Artifact registration from CLI file outputs; records created files as trackable artifacts.
            - **Tags:** `artifacts, registration`
            - **Uses:** `db/*`
        - 📄 **audit.py**
            - **Responsibility:** Audit logging for all CLI operations; records command, result, and metadata per execution.
            - **Tags:** `audit, logging`
            - **Uses:** `db/*`
        - 📄 **types.py**
            - **Responsibility:** Type definitions for CLI execution context and results.
            - **Tags:** `types`
            - **Uses:** `n/a`
        - 📄 **filesystem.py**
            - **Responsibility:** Filesystem utilities (path slugification, normalization) for virtual storage operations.
            - **Tags:** `utilities, filesystem`
            - **Uses:** `n/a`
    - 📁 **llm/** — LLM client abstraction, context assembly, and model routing.
        - 📄 **client.py**
            - **Responsibility:** Unified async LLM client via litellm; abstracts provider differences, manages concurrency semaphore, provides token counting, handles provider-specific body parameters.
            - **Tags:** `llm, client, litellm, async, concurrency`
            - **Uses:** `core/config.py`
        - 📄 **context_builder.py**
            - **Responsibility:** Builds the full LLM message list by resolving 80+ template variables (personality, agent name, role, world status, activity, task, task board, team directory, communication snapshot) from settings and runtime state.
            - **Tags:** `context, prompt-building, templates`
            - **Uses:** `core/config.py, core/models/*, core/tasking/*, core/prompting/*, db/*`
        - 📄 **routing.py**
            - **Responsibility:** Model selection matrix; resolves appropriate model per agent and activation mode (social/work/reasoning/extraction/self_queue); agent override → global setting → None.
            - **Tags:** `routing, model-selection, config`
            - **Uses:** `core/config.py, core/models/*`
        - 📄 **template_engine.py**
            - **Responsibility:** Prompt template rendering engine with Jinja2-style variable substitution for runtime prompt assembly.
            - **Tags:** `templates, rendering, jinja2`
            - **Uses:** `n/a`
    - 📁 **models/** — Pydantic domain models for all entities (20 files).
        - 📄 **__init__.py**
            - **Responsibility:** Central re-export hub for all Pydantic domain models.
            - **Tags:** `models, exports`
            - **Uses:** `core/models/*.py`
        - 📄 **agent.py**
            - **Responsibility:** Defines Agent (persistent identity/configuration), AgentState (runtime position/activity with waiting/blocked statuses), and API input models; includes per-agent LLM model overrides and Guardian thresholds.
            - **Tags:** `model, agent, pydantic`
            - **Uses:** `n/a`
        - 📄 **task.py**
            - **Responsibility:** Defines Task model with full status lifecycle (pending/accepted/active/waiting/blocked/complete/stalled/abandoned/delegated/declined), work contract fields, notification settings, completion_summary, watchdog timestamps, and TaskCreate API input.
            - **Tags:** `model, task, pydantic`
            - **Uses:** `core/models/notification.py, core/models/work_contract.py`
        - 📄 **task_event.py**
            - **Responsibility:** Defines TaskEvent model for durable task-thread events (comment, clarification, answer, status_update, blocker, completion, assignment, reprioritized, system).
            - **Tags:** `model, task-event, pydantic`
            - **Uses:** `n/a`
        - 📄 **trigger.py**
            - **Responsibility:** Defines AgentTrigger model for durable wake-up events with status tracking (queued/claimed/completed/failed), retry counters, and priority ordering.
            - **Tags:** `model, trigger, pydantic`
            - **Uses:** `n/a`
        - 📄 **activity.py**
            - **Responsibility:** Defines Activity model for durable commitment tracking (work, meeting, conversation, movement, break, social).
            - **Tags:** `model, activity, pydantic`
            - **Uses:** `n/a`
        - 📄 **work_contract.py**
            - **Responsibility:** Defines WorkContract and DeliverableSpec models for structured work output specifications with virtual file paths.
            - **Tags:** `model, work-contract, pydantic`
            - **Uses:** `n/a`
        - 📄 **notification.py**
            - **Responsibility:** Defines Notification model with source channels and notification policies for task events.
            - **Tags:** `model, notification, pydantic`
            - **Uses:** `n/a`
        - 📄 **message.py**
            - **Responsibility:** Defines Message model for inter-agent and system communication; includes HUMAN_SENDER_ID sentinel constant.
            - **Tags:** `model, message, pydantic`
            - **Uses:** `n/a`
        - 📄 **memory.py**
            - **Responsibility:** Defines Setting model for system-wide configuration entries.
            - **Tags:** `model, setting, pydantic`
            - **Uses:** `n/a`
        - 📄 **settings.py**
            - **Responsibility:** Defines AIConnection and AIPersonality models with CRUD input/output variants.
            - **Tags:** `model, ai-connection, ai-personality, pydantic`
            - **Uses:** `n/a`
        - 📄 **channel.py**
            - **Responsibility:** Defines Channel model for team communication channels.
            - **Tags:** `model, channel, pydantic`
            - **Uses:** `n/a`
        - 📄 **channel_response.py**
            - **Responsibility:** Defines ChannelResponse model for channel thread response coordination.
            - **Tags:** `model, channel-response, pydantic`
            - **Uses:** `n/a`
        - 📄 **meeting_session.py**
            - **Responsibility:** Defines MeetingSession model for meeting room orchestration.
            - **Tags:** `model, meeting, pydantic`
            - **Uses:** `n/a`
        - 📄 **meeting_response.py**
            - **Responsibility:** Defines MeetingResponse model for meeting participant responses.
            - **Tags:** `model, meeting-response, pydantic`
            - **Uses:** `n/a`
        - 📄 **cli.py**
            - **Responsibility:** Defines CLI execution models (BossModCliCall, command context).
            - **Tags:** `model, cli, pydantic`
            - **Uses:** `n/a`
        - 📄 **cli_policy.py**
            - **Responsibility:** Defines CLI policy models for command approval tiers.
            - **Tags:** `model, cli-policy, pydantic`
            - **Uses:** `n/a`
        - 📄 **artifact.py**
            - **Responsibility:** Defines Artifact model for file references created by agent CLI operations.
            - **Tags:** `model, artifact, pydantic`
            - **Uses:** `n/a`
        - 📄 **prompt_history.py**
            - **Responsibility:** Defines PromptHistory view models for backend-assembled LLM message lists.
            - **Tags:** `model, prompt-history, pydantic`
            - **Uses:** `n/a`
        - 📄 **runtime.py**
            - **Responsibility:** Defines runtime state models for service coordination.
            - **Tags:** `model, runtime, pydantic`
            - **Uses:** `n/a`
    - 📁 **tasking/** — Task management: board views, resolution logic, and service operations.
        - 📄 **__init__.py**
            - **Responsibility:** Package initialization and public exports for task management.
            - **Tags:** `exports`
            - **Uses:** `core/tasking/*.py`
        - 📄 **board.py**
            - **Responsibility:** Shared board views for tasks — self (open), owned (delegated), delegated (child tasks), and project summary rollups.
            - **Tags:** `tasks, boards, views`
            - **Uses:** `db/*, core/agent_loop/activity_runtime.py, core/models/*`
        - 📄 **resolution.py**
            - **Responsibility:** Conservative board-first task resolution; determines whether to bind to an existing workstream or create a new task based on context matching.
            - **Tags:** `tasks, resolution, matching`
            - **Uses:** `db/*, core/models/*`
        - 📄 **service.py**
            - **Responsibility:** Central task creation/reuse and task-thread operations (append events, bind tasks, delegate); the authoritative entry point for all task mutations.
            - **Tags:** `tasks, service, operations`
            - **Uses:** `db/*, core/tasking/resolution.py, core/models/*`
    - 📁 **prompting/** — Prompt management and validation.
        - 📄 **runtime_prompt_registry.py**
            - **Responsibility:** Runtime prompt text resolver and collection; maps prompt keys to loaded template content for the context builder.
            - **Tags:** `prompts, registry, resolution`
            - **Uses:** `core/default_prompts.py`
        - 📄 **runtime_prompt_lint.py**
            - **Responsibility:** Lint model-facing prompt surfaces for contract consistency; validates templates contain required variables, no deprecated references, and match shipped defaults.
            - **Tags:** `linting, validation, quality`
            - **Uses:** `core/default_prompts.py, core/prompting/runtime_prompt_registry.py`
    - 📁 **runtime/** — Background runtime services and event management.
        - 📄 **events.py**
            - **Responsibility:** Event manager for runtime pub/sub notifications between app and runtime processes.
            - **Tags:** `events, pub-sub`
            - **Uses:** `n/a`
        - 📄 **services.py**
            - **Responsibility:** Background service coordination; manages lifecycle of long-running runtime processes.
            - **Tags:** `services, lifecycle`
            - **Uses:** `n/a`
        - 📄 **worker.py**
            - **Responsibility:** Worker/background task execution infrastructure for async job processing.
            - **Tags:** `workers, async`
            - **Uses:** `n/a`
    - 📁 **world/** — Office environment, spatial logic, and simulation.
        - 📄 **simulation.py**
            - **Responsibility:** Background asyncio simulation loop; every tick advances in-transit agents along paths, checks activation triggers for idle agents, and launches agent turns concurrently.
            - **Tags:** `simulation, async, background-task, movement`
            - **Uses:** `api/websocket.py, core/config.py, core/agent_loop/dispatcher.py, core/world/tilemap.py, db/*`
        - 📄 **tilemap.py**
            - **Responsibility:** Office tilemap definition (28×20 grid) with tile types, room metadata, desk positions; provides map data serialization for frontend and location-based rule enforcement.
            - **Tags:** `tilemap, spatial, rooms, desks, map`
            - **Uses:** `n/a`
        - 📄 **pathfinding.py**
            - **Responsibility:** A* pathfinding on the office tilemap; agents use this to navigate between desks, meeting rooms, and break rooms.
            - **Tags:** `pathfinding, a-star, navigation`
            - **Uses:** `core/world/tilemap.py`

- 📁 **db/** — Modular database layer with hardened CRUD helpers (35 modules).
    - 📄 **__init__.py**
        - **Responsibility:** Barrel export re-exporting all public database functions; consumers import via `import db` and call `db.create_agent()`, etc.
        - **Tags:** `exports, barrel, facade`
        - **Uses:** `db/*.py`
    - 📄 **connection.py**
        - **Responsibility:** SQLite singleton connection lifecycle with thread-local pooling; schema initialization from `schema.sql` on first access; table-rebuild migrations for enum expansions (adding waiting/blocked statuses); seeds defaults on init.
        - **Tags:** `connection, schema, migration, sqlite`
        - **Uses:** `db/schema.sql, db/settings.py, db/ai_personalities.py`
    - 📄 **schema.sql**
        - **Responsibility:** Complete SQLite DDL defining all tables with CHECK constraints, UUID defaults, and timestamp defaults. Includes agent_triggers, task_events, activities, runtime_commands, and all domain tables. Safe to execute on every startup.
        - **Tags:** `schema, ddl, tables`
        - **Uses:** `n/a`
    - 📄 **crud.py**
        - **Responsibility:** Hardened reusable CRUD helpers centralizing all database access — parameterized queries, row-to-dict conversion, Pydantic model validation, column-whitelisted updates, insert_returning.
        - **Tags:** `crud, helpers, parameterized, security`
        - **Uses:** `db/connection.py`
    - 📄 **agents.py**
        - **Responsibility:** Agent and AgentState CRUD; creates companion state row on agent creation; auto-timestamps idle/active transitions.
        - **Tags:** `crud, agents, state`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **agent_triggers.py**
        - **Responsibility:** Durable agent trigger queue CRUD with priority-based dispatch ordering (11-level priority), atomic claim/complete/fail/retry lifecycle, stale trigger requeuing, and backlog counting.
        - **Tags:** `crud, triggers, queue, dispatch`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **messages.py**
        - **Responsibility:** Message CRUD with sender name resolution; batch agent lookups for formatted message history.
        - **Tags:** `crud, messages, formatting`
        - **Uses:** `core/models/*, db/crud.py, db/agents.py`
    - 📄 **tasks.py**
        - **Responsibility:** Task CRUD with integrated work contracts, notification policies/targets, smart owner resolution (assigned_to > requester_id > created_by), and auto-timestamping on status changes including waiting state.
        - **Tags:** `crud, tasks, lifecycle, work-contracts`
        - **Uses:** `core/models/*, db/crud.py, db/task_work_contracts.py, db/task_notification_policies.py, db/task_notification_targets.py`
    - 📄 **task_events.py**
        - **Responsibility:** Durable task-thread event CRUD; logs comments, clarifications, answers, status updates, blockers, completions, assignments with author tracking; batch fetch for recent events per task.
        - **Tags:** `crud, task-events, audit-trail`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **task_work_contracts.py**
        - **Responsibility:** Work contract CRUD for task deliverable specifications; manages file path bindings and completion tracking.
        - **Tags:** `crud, work-contracts, deliverables`
        - **Uses:** `db/crud.py`
    - 📄 **task_notification_policies.py**
        - **Responsibility:** Task notification policy CRUD; configures when/how notifications fire for task events.
        - **Tags:** `crud, notifications, policies`
        - **Uses:** `db/crud.py`
    - 📄 **task_notification_targets.py**
        - **Responsibility:** Task notification target CRUD; configures channel routing for task event notifications.
        - **Tags:** `crud, notifications, targets`
        - **Uses:** `db/crud.py`
    - 📄 **settings.py**
        - **Responsibility:** Settings CRUD and 62+ seed defaults across 7 categories; loads default prompt templates from `prompts/` via `core/default_prompts.py`; includes system prompt, action schema, runtime contracts.
        - **Tags:** `crud, settings, seed, config`
        - **Uses:** `core/models/*, core/default_prompts.py, db/crud.py`
    - 📄 **activities.py**
        - **Responsibility:** Durable activity state CRUD; tracks agent commitments (work, meeting, conversation, movement, break, social) with lifecycle timestamps.
        - **Tags:** `crud, activities, commitments`
        - **Uses:** `db/crud.py`
    - 📄 **activity_log.py**
        - **Responsibility:** Activity log CRUD; records persistent event history for UI activity feed.
        - **Tags:** `crud, activity-log, events`
        - **Uses:** `db/crud.py`
    - 📄 **ai_connections.py**
        - **Responsibility:** AI Connection CRUD for saved LLM provider configurations.
        - **Tags:** `crud, ai-connections, llm-config`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **ai_personalities.py**
        - **Responsibility:** AI Personality CRUD with default personality seeding from `prompts/personalities/`; handles upgrade migrations (e.g., renaming Research Assistant → Research Analyst).
        - **Tags:** `crud, ai-personalities, prompts, seed`
        - **Uses:** `core/models/*, core/default_prompts.py, db/crud.py`
    - 📄 **diagnostics.py**
        - **Responsibility:** Diagnostics CRUD with auto-retention purging; records full trace data per agent turn.
        - **Tags:** `crud, diagnostics, trace, auto-purge`
        - **Uses:** `core/config.py, db/crud.py`
    - 📄 **notifications.py**
        - **Responsibility:** Notification CRUD for task completion/blocker/delegation notifications targeted at human operators.
        - **Tags:** `crud, notifications`
        - **Uses:** `db/crud.py`
    - 📄 **notification_links.py**
        - **Responsibility:** Notification deep-link CRUD; maps notifications to desk UI navigation targets.
        - **Tags:** `crud, notifications, deep-links`
        - **Uses:** `db/crud.py`
    - 📄 **channels.py**
        - **Responsibility:** Channel CRUD for manual team communication channels.
        - **Tags:** `crud, channels`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **channel_response_rounds.py**
        - **Responsibility:** Channel response round CRUD; orchestrates participant response ordering for channel messages.
        - **Tags:** `crud, channels, rounds`
        - **Uses:** `db/crud.py`
    - 📄 **meeting_sessions.py**
        - **Responsibility:** Meeting session CRUD for meeting room orchestration.
        - **Tags:** `crud, meetings, sessions`
        - **Uses:** `core/models/*, db/crud.py`
    - 📄 **meeting_response_rounds.py**
        - **Responsibility:** Meeting response round CRUD; coordinates participant response ordering for meeting sessions.
        - **Tags:** `crud, meetings, rounds`
        - **Uses:** `db/crud.py`
    - 📄 **agent_cli.py**
        - **Responsibility:** Agent CLI state CRUD; tracks virtual working directory and CLI session state per agent.
        - **Tags:** `crud, cli, state`
        - **Uses:** `db/crud.py`
    - 📄 **agent_storage.py**
        - **Responsibility:** Agent personal storage CRUD; manages agent-isolated file storage records.
        - **Tags:** `crud, storage, files`
        - **Uses:** `db/crud.py`
    - 📄 **agent_storage_identities.py**
        - **Responsibility:** Storage allocation index CRUD; maps agent identities to storage partitions.
        - **Tags:** `crud, storage, identities`
        - **Uses:** `db/crud.py`
    - 📄 **agent_prompt_history_policies.py**
        - **Responsibility:** Per-agent context window configuration CRUD; controls prompt history depth and token limits.
        - **Tags:** `crud, prompt-history, config`
        - **Uses:** `db/crud.py`
    - 📄 **cli_policy_rules.py**
        - **Responsibility:** CLI command whitelist/blacklist/approval tier CRUD; defines which commands agents can run freely vs requiring approval.
        - **Tags:** `crud, cli-policy, security`
        - **Uses:** `db/crud.py`
    - 📄 **cli_approval_requests.py**
        - **Responsibility:** Human-in-the-loop CLI command approval request CRUD; gates sensitive operations until human operator approves.
        - **Tags:** `crud, approvals, human-in-loop`
        - **Uses:** `db/crud.py`
    - 📄 **artifacts.py**
        - **Responsibility:** Artifact CRUD for file references created by agents during work.
        - **Tags:** `crud, artifacts, files`
        - **Uses:** `db/crud.py`
    - 📄 **bm_cli_events.py**
        - **Responsibility:** Per-command audit log CRUD for BossMod CLI operations.
        - **Tags:** `crud, audit, cli`
        - **Uses:** `db/crud.py`
    - 📄 **runtime_control.py**
        - **Responsibility:** Dispatcher command queue CRUD; allows API layer to send control commands to the runtime dispatcher.
        - **Tags:** `crud, runtime, commands`
        - **Uses:** `db/crud.py`
    - 📄 **metrics.py**
        - **Responsibility:** Performance metrics CRUD; tracks operational statistics for monitoring.
        - **Tags:** `crud, metrics, monitoring`
        - **Uses:** `db/crud.py`
    - 📄 **unified_feed.py**
        - **Responsibility:** Query helpers for aggregated feeds; combines activity, diagnostics, and notifications into unified timeline views.
        - **Tags:** `crud, feeds, aggregation`
        - **Uses:** `db/crud.py`
    - 📄 **world.py**
        - **Responsibility:** World state assembly (agent + state JOIN) and spatial proximity queries (Manhattan distance).
        - **Tags:** `crud, world, spatial`
        - **Uses:** `db/crud.py`

- 📁 **prompts/** — First-class prompt template library (47 files).
    - 📄 **system_prompt.md**
        - **Responsibility:** Base system role and operating rules for all agent turns; defines agent as virtual office employee; injects personality, world status, activity, task, task board, team directory via template variables.
        - **Tags:** `system-prompt, runtime, agent-behavior`
        - **Uses:** `n/a (rendered by context_builder)`
    - 📄 **runtime_contract_execution.md**
        - **Responsibility:** JSON execution contract schema for resumed/internal agent turns; defines allowed actions (cli, work, msg, taskmsg, assign, walk, mtg, idle, wait, done, block, deleg, drop) with required fields per action type.
        - **Tags:** `execution-contract, json-schema, agent-behavior`
        - **Uses:** `n/a (rendered by action_contract)`
    - 📄 **runtime_contract_decision.md**
        - **Responsibility:** JSON decision contract schema for conversation turns; defines allowed actions per trigger type (reply, accept, clarify, decline, defer, cancel) with context-aware conditional logic.
        - **Tags:** `decision-contract, json-schema, agent-behavior`
        - **Uses:** `n/a (rendered by decision_contract)`
    - 📄 **runtime_block_trigger_event.md**
        - **Responsibility:** Template for rendering trigger event context; formats the triggering event (human_chat, peer_message, task_assigned, etc.) into LLM-readable context blocks.
        - **Tags:** `trigger-context, templating`
        - **Uses:** `n/a (rendered by communication)`
    - 📄 **runtime_block_communication_snapshot.md**
        - **Responsibility:** Template for rendering communication history snapshot (messages, task events) as LLM context.
        - **Tags:** `communication, templating`
        - **Uses:** `n/a`
    - 📄 **runtime_block_conversation_envelope.md**
        - **Responsibility:** Template for wrapping conversation context in a structured envelope for LLM consumption.
        - **Tags:** `conversation, templating`
        - **Uses:** `n/a`
    - 📄 **runtime_block_file_deliverable_guidance.md**
        - **Responsibility:** Template providing file output guidance for agents creating deliverables via CLI.
        - **Tags:** `deliverables, guidance, templating`
        - **Uses:** `n/a`
    - 📁 **internal/** — Internal execution guidance prompts injected during agent turns (31 files).
        - 📄 **loop_execution_continue_work_generic.md**
            - **Responsibility:** Guidance for choosing next work step; directs agent to use `done` when complete, `wait` when blocked, `block` when unable to proceed.
            - **Tags:** `execution-guidance, work`
        - 📄 **loop_execution_continue_conversation.md**
            - **Responsibility:** Guidance for continuing an active conversation activity.
            - **Tags:** `execution-guidance, conversation`
        - 📄 **loop_execution_continue_meeting.md**
            - **Responsibility:** Guidance for continuing a meeting activity.
            - **Tags:** `execution-guidance, meeting`
        - 📄 **loop_execution_continue_break.md**
            - **Responsibility:** Guidance for break activity completion.
            - **Tags:** `execution-guidance, break`
        - 📄 **loop_execution_continue_move_to_desk.md**
            - **Responsibility:** Guidance for desk navigation activity.
            - **Tags:** `execution-guidance, movement`
        - 📄 **loop_execution_continue_generic.md**
            - **Responsibility:** Generic execution continuation guidance.
            - **Tags:** `execution-guidance, generic`
        - 📄 **loop_execution_continue_work_missing_deliverable.md**
            - **Responsibility:** Guidance when required deliverable file is missing from work contract.
            - **Tags:** `execution-guidance, deliverables, validation`
        - 📄 **loop_execution_cli_followup.md**
            - **Responsibility:** Guidance for continuing after a CLI command result in execution turns.
            - **Tags:** `execution-guidance, cli`
        - 📄 **loop_decision_cli_followup.md**
            - **Responsibility:** Guidance for continuing after a CLI command result in decision turns.
            - **Tags:** `decision-guidance, cli`
        - 📄 **loop_decision_repair_primary.md**
            - **Responsibility:** Primary repair prompt when agent's JSON response fails validation.
            - **Tags:** `repair, validation, json`
        - 📄 **loop_decision_repair_preserve_intent.md**
            - **Responsibility:** Repair prompt variant that preserves agent's original intent while fixing JSON format.
            - **Tags:** `repair, validation, intent-preservation`
        - 📄 **loop_decision_repair_keys.md**
            - **Responsibility:** Repair prompt specifically for missing/extra JSON keys in agent responses.
            - **Tags:** `repair, validation, keys`
        - 📄 **loop_approval_review_followup.md**
            - **Responsibility:** Guidance for agent after a CLI approval request is reviewed by human operator.
            - **Tags:** `approval, human-in-loop`
        - 📄 **loop_approval_rejected_result.md**
            - **Responsibility:** Guidance when a CLI approval request is rejected by human operator.
            - **Tags:** `approval, rejection`
        - 📄 **cli_authoritative_status.md**
            - **Responsibility:** Directive to use CLI snapshot as authoritative current state for the turn.
            - **Tags:** `cli, authority`
        - 📄 **cli_authoritative_activity.md**
            - **Responsibility:** Authoritative activity state from CLI introspection.
            - **Tags:** `cli, authority, activity`
        - 📄 **cli_authoritative_current_task.md**
            - **Responsibility:** Authoritative current task context from CLI introspection.
            - **Tags:** `cli, authority, task`
        - 📄 **cli_authoritative_tasks.md**
            - **Responsibility:** Authoritative task board state from CLI introspection.
            - **Tags:** `cli, authority, tasks`
        - 📄 **cli_authoritative_recent_work.md**
            - **Responsibility:** Authoritative recent work artifacts from CLI introspection.
            - **Tags:** `cli, authority, artifacts`
        - 📄 **cli_authoritative_runtime.md**
            - **Responsibility:** Authoritative runtime state from CLI introspection.
            - **Tags:** `cli, authority, runtime`
        - 📄 **cli_authoritative_location.md**
            - **Responsibility:** Authoritative agent location from CLI introspection.
            - **Tags:** `cli, authority, location`
        - 📄 **cli_approval_pause_note.md**
            - **Responsibility:** Notification that agent is paused awaiting CLI approval from human operator.
            - **Tags:** `cli, approval, pause`
        - 📄 **cli_result_wrapper.md**
            - **Responsibility:** Template for wrapping CLI command results into LLM context format.
            - **Tags:** `cli, results, templating`
        - 📄 **action_requires_workspace.md**
            - **Responsibility:** Directive to walk to desk first before performing work actions.
            - **Tags:** `action-guard, workspace`
        - 📄 **action_large_work_single_file_guidance.md**
            - **Responsibility:** Guidance for large single-file work output using BossMod CLI write commands.
            - **Tags:** `work-guidance, large-files`
        - 📄 **action_large_work_multi_file_guidance.md**
            - **Responsibility:** Guidance for multi-file work output using BossMod CLI `bwrite` with manifest (not inline data.out).
            - **Tags:** `work-guidance, multi-file, managed-writer`
        - 📄 **managed_writer_single_pass.md**
            - **Responsibility:** Managed writer instruction for single-pass file output; includes done_sentinel and plan_sentinel tokens.
            - **Tags:** `managed-writer, single-pass`
        - 📄 **managed_writer_section_plan.md**
            - **Responsibility:** Managed writer instruction for section-by-section planning approach.
            - **Tags:** `managed-writer, section-plan`
        - 📄 **managed_writer_section.md**
            - **Responsibility:** Managed writer instruction for writing an individual section.
            - **Tags:** `managed-writer, section`
        - 📄 **managed_writer_section_rewrite.md**
            - **Responsibility:** Managed writer instruction for rewriting a completed section.
            - **Tags:** `managed-writer, rewrite`
        - 📄 **managed_writer_error_guidance.md**
            - **Responsibility:** Managed writer error recovery guidance.
            - **Tags:** `managed-writer, error-recovery`
    - 📁 **personalities/** — Agent role templates (10 files).
        - 📄 **default_role.md** — Minimal base: professional, focused employee.
        - 📄 **software_engineer.md** — Senior full-stack developer; no pseudocode/stubs/TODOs; reviewable output.
        - 📄 **project_manager.md** — Cross-functional coordination; milestones, risks, dependencies, timelines.
        - 📄 **research_analyst.md** — Methodical evidence gathering; bias awareness; source attribution.
        - 📄 **growth_marketer.md** — Experiment design; market understanding; conversion focus.
        - 📄 **ui_ux_designer.md** — User empathy; accessibility; design systems.
        - 📄 **data_analyst.md** — Data quality; statistical rigor; storytelling.
        - 📄 **qa_engineer.md** — Test coverage; edge cases; automation mindset.
        - 📄 **technical_writer.md** — Clarity; progressive disclosure; examples.
        - 📄 **creative_writer.md** — Narrative voice; emotional resonance; constraints.

- 📁 **ui/** — Server-rendered SPA frontend.
    - 📁 **templates/**
        - 📄 **index.html**
            - **Responsibility:** Jinja2 root template; defines DOM structure with top nav (Office/Company mode toggle), resizable panels (Split.js), canvas container, sub-view containers (Chat, Edit, Tasks, Diagnostics), Company mode tabs (Files, Tasks, Metrics, Org Chart), settings overlay, and mobile bottom sheet. Loads CDN deps + bundled vendor libs (highlight.js, marked.js).
            - **Tags:** `template, html, layout, jinja2`
            - **Uses:** `ui/static/css/style.css, ui/static/js/*.js`
    - 📁 **static/css/**
        - 📄 **style.css**
            - **Responsibility:** Custom component styles extending Tailwind — tab states, Split.js gutters, chat bubbles, diagnostic cards, activity entries, mobile bottom sheet, scrollbar styling.
            - **Tags:** `css, styling, components`
            - **Uses:** `n/a`
    - 📁 **static/js/** — Application JavaScript modules (19 app files + 13 vendor files).
        - 📄 **app.js**
            - **Responsibility:** Main application controller; bootstraps UI, manages WebSocket connection with auto-reconnect, routes WebSocket events to modules, handles top-level navigation (Office/Company mode) and Split.js panel persistence.
            - **Tags:** `controller, bootstrap, websocket, routing`
            - **Uses:** `utils.js, canvas.js, activity.js, agent-context.js, diagnostics.js, settings-view.js, company-view.js`
        - 📄 **agent-context.js**
            - **Responsibility:** Manages selected agent state and left panel UI; handles agent selection/deselection, tab switching between sub-views (Chat, Edit, Tasks, Diagnostics), chat message sending/display, agent task list rendering, and runtime status display.
            - **Tags:** `controller, agent-selection, chat, subviews`
            - **Uses:** `utils.js, agent-panel.js, diagnostics.js`
        - 📄 **agent-panel.js**
            - **Responsibility:** Agent creation/editing form; handles personality/connection resolution, color/desk selection, CRUD operations, and form data transformation for API submission.
            - **Tags:** `controller, form, agent-crud, ui`
            - **Uses:** `utils.js, settings-view.js`
        - 📄 **canvas.js**
            - **Responsibility:** 2D canvas office map renderer; draws tilemap, room labels, desk monitors, and agent circles with status indicators; handles click-to-select and hover effects.
            - **Tags:** `canvas, rendering, tilemap, interaction`
            - **Uses:** `utils.js, app.js`
        - 📄 **activity.js**
            - **Responsibility:** Activity log display; receives WebSocket events, renders with event-specific icons and color-coded badges, maintains max 100 entries.
            - **Tags:** `controller, activity-log, events`
            - **Uses:** `utils.js`
        - 📄 **diagnostics.js**
            - **Responsibility:** Diagnostics tab controller; renders compact summary cards per agent turn, lazy-loads full trace details on expand, handles WebSocket diagnostic events.
            - **Tags:** `controller, diagnostics, trace-viewer`
            - **Uses:** `utils.js`
        - 📄 **settings-view.js**
            - **Responsibility:** Full-screen settings view with 2-column layout (left nav + right content); sections for AI Connections (CRUD + test), AI Personalities (CRUD), System Settings, CLI Policy, Telegram config, Advanced, System Prompt Template, Runtime Contracts.
            - **Tags:** `controller, settings, connections, personalities, config-ui`
            - **Uses:** `utils.js, app.js, cli-policy-section.js`
        - 📄 **cli-policy-section.js**
            - **Responsibility:** CLI policy management UI section; configures command whitelist/blacklist/approval tiers for agent CLI operations.
            - **Tags:** `controller, cli-policy, security-ui`
            - **Uses:** `utils.js`
        - 📄 **channels-view.js**
            - **Responsibility:** Channels view controller; manages team communication channel UI with message threading and participant management.
            - **Tags:** `controller, channels, communication`
            - **Uses:** `utils.js`
        - 📄 **company-view.js**
            - **Responsibility:** Company mode container controller; manages tab switching between Files, Tasks, Metrics, and Org Chart sub-views.
            - **Tags:** `controller, company-mode, tabs`
            - **Uses:** `company-files.js, company-tasks.js, company-metrics.js, company-org.js, company-dashboard.js`
        - 📄 **company-tasks.js**
            - **Responsibility:** Organization-wide task board; displays all tasks with status filtering, agent filtering, search with debounce, expandable detail rows, real-time WebSocket refresh, and footer summary statistics.
            - **Tags:** `controller, task-board, company-view`
            - **Uses:** `utils.js`
        - 📄 **company-files.js**
            - **Responsibility:** Company file browser; navigates agent storage directories with breadcrumb trails, file/folder icons, and click-to-view.
            - **Tags:** `controller, file-browser, company-view`
            - **Uses:** `utils.js, company-file-viewer.js, company-file-ops.js`
        - 📄 **company-file-viewer.js**
            - **Responsibility:** Modal overlay for viewing/editing files with syntax highlighting (highlight.js), markdown preview (marked.js), image preview, JSON pretty-printing, and binary file detection.
            - **Tags:** `controller, file-viewer, syntax-highlight, preview`
            - **Uses:** `utils.js`
        - 📄 **company-file-ops.js**
            - **Responsibility:** File operation handlers for create/delete/rename operations in the company file browser.
            - **Tags:** `controller, file-operations`
            - **Uses:** `utils.js`
        - 📄 **company-metrics.js**
            - **Responsibility:** Company metrics dashboard displaying operational statistics and agent performance data.
            - **Tags:** `controller, metrics, dashboard`
            - **Uses:** `utils.js`
        - 📄 **company-org.js**
            - **Responsibility:** Organization chart view displaying agent team structure and roles.
            - **Tags:** `controller, org-chart`
            - **Uses:** `utils.js`
        - 📄 **company-dashboard.js**
            - **Responsibility:** Company dashboard overview with summary cards and quick-access widgets.
            - **Tags:** `controller, dashboard, overview`
            - **Uses:** `utils.js`
        - 📄 **tailwind-config.js**
            - **Responsibility:** Tailwind CSS runtime configuration with custom theme extensions.
            - **Tags:** `config, tailwind, theming`
            - **Uses:** `n/a`
        - 📄 **utils.js**
            - **Responsibility:** Shared utility functions — HTML escaping (XSS prevention), agent data normalization, status color/class/dot/label mappings, relative time formatting, overlay panel animations.
            - **Tags:** `utilities, helpers, xss-prevention`
            - **Uses:** `n/a`
        - 📁 **vendor/** — Bundled third-party libraries (offline-compatible, no CDN).
            - 📄 **highlight.min.js** — Syntax highlighting engine.
            - 📄 **hljs-lang-{bash,css,ini,javascript,json,markdown,python,sql,typescript,xml,yaml}.min.js** — 11 language packs for highlight.js.
            - 📄 **marked.min.js** — Markdown rendering engine.

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

- 📁 **integrations/** — External platform integrations.
    - 📁 **telegram/** — Telegram bot integration for remote agent interaction.
        - 📄 **bot.py**
            - **Responsibility:** Telegram bot initialization and command handlers; bridges Telegram messages to agent chat triggers.
            - **Tags:** `telegram, bot, commands`
            - **Uses:** `integrations/telegram/bridge.py, integrations/telegram/sessions.py`
        - 📄 **bridge.py**
            - **Responsibility:** Bridges Telegram messages to BossMod agent activation; translates between Telegram and internal message formats.
            - **Tags:** `telegram, bridge, messaging`
            - **Uses:** `db/*, api/*`
        - 📄 **formatters.py**
            - **Responsibility:** Telegram message formatting utilities; converts internal data structures to Telegram-compatible markdown/HTML.
            - **Tags:** `telegram, formatting`
            - **Uses:** `n/a`
        - 📄 **sessions.py**
            - **Responsibility:** Telegram session management; tracks active chat sessions and agent bindings per Telegram user.
            - **Tags:** `telegram, sessions, state`
            - **Uses:** `db/*`

- 📁 **tests/** — Integration and validation test suite.
    - 📄 **test_agent_runtime.py**
        - **Responsibility:** End-to-end agent runtime tests covering turn execution, CLI integration, task workflow, meetings, managed writer, artifact registration, delegation chains. 80+ test cases.
        - **Tags:** `tests, integration, agent-loop, e2e`
        - **Uses:** `core/*, db/*`
    - 📄 **test_runtime_prompt_consistency.py**
        - **Responsibility:** Validates prompt health, consistency, and contract compliance; checks default prompts are clean, override prompts match shipped defaults, deprecated template variables are absent.
        - **Tags:** `tests, prompt-validation, lint`
        - **Uses:** `core/prompting/*, core/default_prompts.py`

- 📁 **scripts/** — Development and CI utilities.
    - 📄 **run_runtime_smoke_suite.sh**
        - **Responsibility:** Runs subset of 11 critical integration tests as a fast smoke suite for CI and pre-merge validation.
        - **Tags:** `ci, smoke-tests, script`
        - **Uses:** `tests/test_agent_runtime.py`

- 📁 **docs/** — Design specifications and implementation plans.
    - 📁 **superpowers/specs/**
        - 📄 **2026-03-21-diagnostics-design.md**
            - **Responsibility:** Full design specification for the diagnostics feature.
            - **Tags:** `spec, design, diagnostics`
            - **Uses:** `n/a`
    - 📁 **superpowers/plans/**
        - 📄 **2026-03-21-diagnostics.md**
            - **Responsibility:** Step-by-step implementation plan for diagnostics feature.
            - **Tags:** `plan, implementation, diagnostics`
            - **Uses:** `n/a`

- 📁 **strategy-docs/** — Vision documents, analysis reports, and enhancement proposals.
    - 📄 **BossMod_AI_Vision.docx** — High-level project vision and roadmap.
    - 📄 **BossMod_AI_Vision_v2.md** — Updated vision document (markdown).
    - 📄 **BossMod_Enhancement_v1.docx** — Enhancement proposals for future development.
    - 📄 **scenario_matrix_evals.md** — Scenario-based evaluation matrix for agent runtime behavior.
    - 📄 **system_prompt.md** — System prompt reference/analysis document.
    - 📄 **pixel_generator_prompt.md** — Sprite sheet generation prompt for character pixel art assets.
    - 📄 **taskSystemRedesign_analysis_03302026_164729_139.md** — Task system redesign analysis.
    - 📄 **taskSystemCloseout_audit_03302026_174512_421.md** — Task system closeout audit.
    - 📄 **taskPromptCloseoutReaudit_audit_03302026_181449_489.md** — Task prompt closeout re-audit.
    - 📄 **taskCutover_reassessment_analysis_03302026_190512_001.md** — Task cutover reassessment.
    - 📄 **taskCommitmentIntegrity_audit_03302026_203100_001.md** — Task commitment integrity audit.
    - 📄 **taskAgentLoop_analysis_03222026_124647152.md** — Agent loop task analysis.
    - 📄 **agentPromptContracts_analysis_03292026_205317285.md** — Agent prompt contracts analysis.
    - 📄 **runtimeContracts_analysis_03252026_182010943.md** — Runtime contracts analysis.
    - 📄 **runtimeIsolation_analysis_03262026_090717090.md** — Runtime isolation analysis.
    - 📄 **runtimeProcessIsolation_analysis_03262026_102503_389.md** — Runtime process isolation analysis.
    - 📄 **aiAutonomyContext_analysis_03212026_205027523.md** — AI autonomy context analysis.
    - 📄 **deskMarkdownLatency_analysis_03252026_233551947.md** — Desk markdown latency analysis.

- 📁 **artifacts/** — Sample agent configurations and project templates.
- 📁 **plugins/** — Plugin directory (reserved for future use).
