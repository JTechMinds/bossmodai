# BossMod AI — Current-state architecture

**As of:** current tree after PR #2 and the HA-* health landings (2026-09-05). This is a map of what exists, not a target design. [`HEALTH_AUDIT.md`](HEALTH_AUDIT.md) is a **snapshot** of `main` @ `f5405bc` and is not current for security status.

## Process topology

```mermaid
flowchart LR
  subgraph desktop [Desktop process]
    Tauri["Tauri shell<br/>desktop/src/main.rs"]
  end

  subgraph app [FastAPI process]
    Main["main.py lifespan"]
    API["api/routes/"]
    WS["api/websocket.py manager"]
    RS["core/runtime/services.py<br/>runtime_services"]
    TG["integrations/telegram"]
  end

  subgraph worker [Runtime worker process]
    W["core/runtime/worker.py"]
    Disp["dispatcher"]
    Loop["agent_loop/loop.py"]
    Sim["world/simulation"]
    WD["task + meeting watchdogs"]
  end

  DB[(SQLite WAL<br/>bossmod.sqlite3)]

  Tauri -->|spawns| Main
  Main --> API
  Main --> RS
  Main --> TG
  RS -->|JSONL stdout events| WS
  RS -->|runtime_commands rows| DB
  W -->|claim commands + heartbeat| DB
  Disp --> Loop
  Loop --> DB
  Sim --> DB
  API --> DB
  TG --> DB
```

Two Python processes share one SQLite file. The app process owns HTTP, WebSocket, Telegram, and settings. The worker owns the agent loop, world tick, and watchdogs. They coordinate through `runtime_commands` / `runtime_worker_state` plus a JSONL event pipe on the worker's stdout.

## Boot → first agent turn

```mermaid
sequenceDiagram
  participant T as Tauri / run.sh
  participant A as FastAPI (main.py)
  participant R as RuntimeServices
  participant W as Worker process
  participant D as TurnDispatcher
  participant L as run_turn
  participant LLM as litellm

  T->>A: spawn python main.py
  A->>A: init_db + seed settings/personalities/CLI rules
  A->>R: runtime_services.start()
  R->>W: python -m core.runtime.worker
  W->>D: dispatcher.start()
  W-->>R: JSONL ready
  A->>A: optional Telegram start
  Note over A: UI connects GET / and /api/ws
  A->>R: enqueue_trigger(human_chat / task_assigned)
  R->>W: wake_dispatcher command
  D->>D: claim_trigger (queued → claimed)
  D->>L: run_turn
  alt decision trigger
    L->>LLM: decision contract
    L->>L: decision_runtime.apply_decision
  else execution trigger
    L->>LLM: execution contract
    L->>L: actions.execute_action → bm_cli / walk / done / ...
  end
  L-->>W: events (chat, world, diagnostic, feed)
  W-->>R: JSONL event
  R->>A: websocket + Telegram bridge
```

## Major packages

| Package | Role today |
| --- | --- |
| `main.py` | FastAPI app, lifespan, `/` + `/health`, binds `127.0.0.1:38471` |
| `api/` | Split routers under `api/routes/` (`ws`, `agents`, `tasks`, `company_files`, `settings`, `cli_policy`, `runtime`) + WebSocket manager. Largest leftover: `agents.py` (~808 LOC). |
| `core/runtime/` | App↔worker gateway (`RuntimeServices` singleton) and worker loop |
| `core/agent_loop/` | Trigger dispatch, decision/execution turns, meetings, channels, watchdogs |
| `core/bm_cli/` | Virtual CLI + optional host shell + managed writer |
| `core/tasking/` | Create/bind tasks, board, resolution |
| `core/llm/` | Context assembly, template engine, litellm client, routing |
| `core/world/` | Tilemap + A* movement tick |
| `core/config.py` | Process-wide settings cache over `settings` table |
| `db/` | SQLite access; `db/__init__.py` re-exports ~200 symbols |
| `integrations/telegram/` | Bot commands, in-memory chat sessions, approval buttons |
| `ui/` | Vanilla JS IIFEs + vendored Tailwind/Lucide/Split + Jinja `index.html` |
| `desktop/` | Tauri 2 wrapper; records backend PID and signals that process on relaunch |
| `prompts/` | Authored contracts, personalities, internal repair/continue text |
| `plugins/` | Empty directory (no plugin loader) |

## Coupling hotspots

- **God objects / process globals:** `runtime_services`, `dispatcher`, `simulation`, `watchdog`, `meeting_watchdog`, `policy_engine`, `manager` (WebSocket), `runtime_events`, `config._cache`.
- **Layer inversion:** `RuntimeServices` broadcasts through an `EventSink` set in `main.py` lifespan (`set_event_sink(manager)`). `core/` does not import `api`. Worker code still aliases `runtime_events as manager` so the same call sites work in both processes.
- **Barrel import:** almost all persistence goes `import db` → `db/__init__.py`. Easy to call, hard to fake in tests.
- **Dual enqueue:** API/Telegram use `runtime_services.enqueue_trigger` (cross-process). Worker-side watchdogs/meetings use `dispatcher.enqueue_trigger` (in-process). Correct given the split; easy to call the wrong one from new code.
- **Lazy imports** inside functions (`from core.world.simulation import simulation`, `from core.bm_cli.runtime import execute_approved_command`) paper over cycles rather than invert dependencies.

## Trust boundaries (current tree)

```mermaid
flowchart TB
  subgraph localAuth [Local API token — PR #2, on main]
    REST["/api/* requires X-BossMod-Token or Bearer"]
    WSep["/api/ws?token= for WebSocket"]
    Health["GET /health and HTML/static stay open"]
  end
  subgraph telegram [Telegram — PR #2, fail-closed]
    Allow["empty allowlist = deny-all; bot will not start"]
  end
  subgraph shell [Host shell if enabled]
    Exec["subprocess.run shell=False + argv path jail + hardened seed"]
  end
  subgraph files [Company files]
    Projects["rooted at artifacts/projects; backups and raw agent dirs denied"]
  end
```

**Shipped on `main` (do not describe as open):**

- **PR #2** — local API token (`X-BossMod-Token` / `Authorization: Bearer`) on `/api` REST and WebSocket; settings/connections redact secrets; Telegram empty allowlist is deny-all and refuses start.
- **HA-SEC-P0-04** — company browser root is `artifacts/projects`; `db_backups/` and raw `agents/` are outside it.
- **HA-SEC-P0-03 / HA-SEC-P1-04** — shell path jail; interpreters / `xargs` / POSIX shells are `never_allowed`; argv[0] basename matching.

Residual (not “auth is missing”): `/health` and the HTML/static UI stay unauthenticated by design. Connection-test URLs are allowlisted (HA-SEC-NEW-01, shipped). Every HA-* item in [`HEALTH_BACKLOG.md`](HEALTH_BACKLOG.md) is shipped on current `main`; that file is the historical sequence plus out-of-scope notes, not an open queue. See [`HEALTH_VERIFICATION.md`](HEALTH_VERIFICATION.md) for the latest verification pass.
