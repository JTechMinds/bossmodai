# BossMod AI — Current-state architecture

**As of:** `main` @ `f5405bc` (2026-09-05). This is a map of what exists, not a target design.

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
| `api/` | One 2.3k-LOC router + WebSocket manager |
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

## Trust boundaries (current `main`)

```mermaid
flowchart TB
  subgraph unauth [No auth on main]
    REST["/api/* including settings, keys, reseed, simulator"]
    WSep["/api/ws"]
    Health["/health"]
  end
  subgraph telegram [Telegram]
    Allow["_check_auth: empty allowlist = allow all"]
  end
  subgraph shell [Host shell if enabled]
    Exec["subprocess.run shell=False, no path jail"]
  end
  subgraph files [Company files]
    Artifacts["rooted at artifacts/ including db_backups/"]
  end
```

PR #2 (`cursor/sec-p0-01-p0-02-b82e`, open) adds a local API token and fail-closed Telegram allowlist. It does **not** change the shell jail or company-files root. See `docs/HEALTH_AUDIT.md`.
