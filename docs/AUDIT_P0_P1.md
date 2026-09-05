# BossMod AI — Forensic Audit (P0 / P1)

**This file is a snapshot, not current status.** It records `main` @ `f5405bc`. **PR #2 is merged:** local API token + Telegram fail-closed + redaction are on `main`. Later HA-* items closed the shell jail, company-files root, and much of the test gap. For current state see [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`HEALTH_BACKLOG.md`](HEALTH_BACKLOG.md).

**Repo:** https://github.com/JTechMinds/bossmodai  
**Branch audited:** `main` @ `f5405bc` (“loop bugfixes”, 2026-09-05)  
**Method:** Remote-only via `gh api` / contents fetches (no clone, no repo edits)  
**Auditor context:** Jordan / jordandevai / JTechMinds  

---

## Executive summary

- Trust model is **localhost desktop**: FastAPI binds `127.0.0.1:38471` by default and Tauri forces the same. There is **no API authentication** on REST or WebSocket.
- **Secrets are stored and returned in plaintext**: `settings.telegram_bot_token`, `ai_connections.api_key`, per-agent `agents.api_key` (via dedicated endpoint). `GET /api/settings` and `GET /api/connections` return full values.
- **Telegram fails open**: if `telegram_allowed_user_ids` is empty, `_check_auth` allows **any** Telegram user. That includes chatting with agents and **approving/rejecting shell commands**.
- **Shell path is not a sandbox**: `shell=False` + env allowlist are real mitigations, but there is **no filesystem/network jail**. Seed `always_allowed` includes `python` / `python3` / `node` / `cat` / `find` / `xargs`. When `cli_shell_enabled=true`, agents can escape the workspace via absolute paths or interpreters.
- **Company file API roots at `artifacts/`**, which also holds `db_backups/` created by `reset_database()` — SQLite backups containing the same secrets.
- Virtual `/me` / `/projects` path resolution (`resolve_relative_path` / `normalize_cli_path`) is **solid against classic `../` traversal** for the virtual CLI.
- Agent loop claiming (`UPDATE … WHERE status='queued'`) is reasonable; **stale claim requeue** after `trigger_claim_timeout_seconds` (default 300) can still duplicate long turns.
- **Tests are critically thin**: only `tests/test_meeting_orchestrator.py` — no coverage for policy engine, shell executor, Telegram auth, secret redaction, or path jail.
- Prompt stack has constrained templating (good) but **elevates BM CLI output to `role=system`**, amplifying injection from tool output / file contents.
- Desktop first-run (`run.sh` + Tauri) is straightforward; main residual risk is binding/host override (`BOSSMOD_HOST`) and unauthenticated local API surface.

---

## Findings

### P0

#### SEC-P0-01 — Telegram allowlist fails open
| Field | Detail |
| --- | --- |
| **Severity** | P0 |
| **Area** | Telegram trust boundary |
| **Evidence** | `integrations/telegram/bot.py` `_check_auth`: if `telegram_allowed_user_ids` is empty/falsy, returns `True`. Seed default in `db/settings.py` is `("", "telegram")`. Approval handlers (`cmd_approve`, `handle_approval_callback`) use the same gate. |
| **Impact** | Anyone who can message the bot gains operator-equivalent control: DM/group chat into the agent runtime, list agents/channels, and **approve shell commands** that then run via `execute_approved_command` (policy bypass). |
| **Fix direction** | Fail closed: require a non-empty allowlist when `telegram_enabled=true`; refuse start if missing. Treat empty allowlist as deny-all. Add integration tests for unauthorized users. |

#### SEC-P0-02 — Unauthenticated API returns secrets + destructive controls
| Field | Detail |
| --- | --- |
| **Severity** | P0 |
| **Area** | API / secrets |
| **Evidence** | `main.py` mounts `api_router` with no auth middleware. `GET /api/settings` → `db.get_settings()` returns raw values including `telegram_bot_token`. `AIConnection.api_key` is **not** `exclude=True` (`core/models/settings.py`); `GET /api/connections` returns keys. `GET /api/agents/{id}/api-key` returns `{"api_key": ...}`. Destructive: `POST /api/settings/reseed-application` → `reset_database()`, `POST /api/settings/reseed`, `PUT /api/runtime/state`, `POST /api/cli-policy/simulator/execute`. WebSocket `/api/ws` accepts with no challenge. |
| **Impact** | On any multi-user host, compromised browser/extension, or `BOSSMOD_HOST=0.0.0.0`, an attacker can steal LLM/Telegram keys, wipe the DB, pause runtime, or run CLI as any agent. Even on single-user localhost this is CSRF-adjacent from malicious pages hitting `127.0.0.1:38471`. |
| **Fix direction** | Redact secrets in list/get APIs (return `has_api_key` / last-4 only). Add a local auth gate (token in settings, or Tauri-issued session). Block destructive routes behind confirmation + auth. Never return full `telegram_bot_token` after write. |

#### SEC-P0-03 — Enabling shell grants host escape (seed policy + no path jail)
| Field | Detail |
| --- | --- |
| **Severity** | P0 (latent until `cli_shell_enabled=true`; becomes active immediately when toggled) |
| **Area** | bm_cli shell / policies |
| **Evidence** | `core/bm_cli/shell_executor.py` runs `subprocess.run(args, cwd=workspace, env=sanitized, shell=False)` — **cwd is not a chroot**; absolute paths work. `_execute_shell` in `runtime.py` only sets cwd to agent artifact dir. Seed rules in `db/cli_policy_rules.py` mark as **`always_allowed`**: `python`, `python3`, `node`, `cat`, `find`, `ls`, **`xargs`**, etc. Default `cli_shell_enabled` is `"false"` (`db/settings.py`). `execute_approved_command` bypasses policy entirely after approval. |
| **Impact** | With shell enabled (or after Telegram/UI approval of a dangerous command), an agent (or attacker driving an agent) can read host files (`cat /etc/passwd`, `python -c 'open(...).read()'`), exfiltrate via approved `curl`, or chain `xargs` to run blocked binaries. |
| **Fix direction** | Keep shell off by default (already). Remove interpreters/`xargs` from `always_allowed`; require approval + path allowlist. Enforce arg path confinement (reject absolute paths outside workspace). Prefer bubblewrap/landlock/Firejail or a dedicated runner user. Block `bash`/`sh`/`zsh` explicitly in `never_allowed`. |

#### SEC-P0-04 — Company files API can expose DB backups under `artifacts/`
| Field | Detail |
| --- | --- |
| **Severity** | P0 |
| **Area** | Desktop/backend file boundary |
| **Evidence** | Company routes (`api/routes.py`) use `artifacts_root()` for list/read/raw/delete. `db/connection.py` `reset_database()` copies SQLite to `artifacts/db_backups/<db>.<stamp>.bak`. Schema stores `api_key` / settings secrets in that DB. Path guard `_resolve_safe_company_path` correctly contains paths **within** artifacts, but that root is too wide. |
| **Impact** | Operator UI or unauthenticated local client can download DB backups and extract Telegram/LLM keys offline. |
| **Fix direction** | Mount company browser at `artifacts/projects` only (or a dedicated share root). Keep `db_backups/` and `agents/` outside the company tree. Deny serving `*.bak` / sqlite files. |

---

### P1

#### SEC-P1-01 — BM CLI / tool output elevated to `role=system`
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | LLM / prompt injection |
| **Evidence** | `core/agent_loop/loop.py` appends `{"role": "system", "content": result["cli_prompt_content"]}` (and similarly for managed/approved CLI paths). `core/llm/context_builder.py` otherwise uses user/assistant for history. |
| **Impact** | File contents or shell stdout can override instructions more easily than user-role content (classic tool-output injection). |
| **Fix direction** | Keep tool results as `user` or a dedicated `tool` role with hard delimiters; never `system`. Add a lint/test that forbids system-role CLI wrapping. |

#### SEC-P1-02 — Stale trigger requeue can duplicate in-flight turns
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | Agent loop correctness |
| **Evidence** | `db/agent_triggers.py` `requeue_stale_triggers` flips `claimed` → `queued` solely by `claimed_at` age. Dispatcher (`dispatcher.py`) runs turns concurrently per agent via `_active_turns`, but a long LLM/shell turn (> `trigger_claim_timeout_seconds`, default 300 from settings) can be requeued while still executing, then claimed again. |
| **Impact** | Duplicate side effects: double messages, double writes, double spends, meeting/state races. |
| **Fix direction** | Heartbeat `claimed_at` during turns; only requeue if worker/process dead; use lease tokens; cancel old task on reclaim. |

#### SEC-P1-03 — CLI approval ID prefix matching is ambiguous
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | Telegram / approvals |
| **Evidence** | `integrations/telegram/bot.py` `_resolve_approval_by_prefix` returns the **first** pending request whose UUID `startswith` the prefix. |
| **Impact** | Short prefixes can approve the wrong command (especially dangerous with SEC-P0-01). |
| **Fix direction** | Require unique match or full UUID; reject ambiguous prefixes. |

#### SEC-P1-04 — Policy bypass vectors when shell is on (`xargs`, missing `bash`/`sh`)
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | bm_cli policies |
| **Evidence** | Seed `always_allowed` includes `xargs`. Seed `never_allowed` covers `sudo`, `rm -rf /`, etc., but **not** `bash`/`sh`/`zsh`. Policy matches on full command string prefix — `xargs` can invoke denied tools. |
| **Impact** | Agents (or prompt injection) can circumvent never_allowed/approval tiers. |
| **Fix direction** | Move `xargs` to never_allowed or approval_required; add shell interpreters to never_allowed; evaluate argv[0] after resolve, not only raw string. |

#### SEC-P1-05 — Secrets at rest are plaintext SQLite; Agent model hide is incomplete
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | DB / secrets |
| **Evidence** | `db/schema.sql`: `agents.api_key`, `ai_connections.api_key`, `settings` key/value. `Agent.api_key` uses `Field(exclude=True)` but dedicated route + connections model still leak. No encryption layer. |
| **Impact** | Disk theft / backup exposure / company-files path (SEC-P0-04) yields all credentials. |
| **Fix direction** | OS keychain / age encryption for secret columns; redact all API serializers; scrub logs (`shell_executor` logs full `command=%r`). |

#### SEC-P1-06 — Unauthenticated CLI simulator executes real pipeline
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | API / agent tool surface |
| **Evidence** | `POST /api/cli-policy/simulator/execute` calls `execute_bm_cli(...)` for a chosen `agent_id` with no auth. |
| **Impact** | Local caller can run virtual writes / allowed shell as any agent, mutating workspaces and creating approval spam. |
| **Fix direction** | Auth-gate; default dry-run; separate simulate vs execute permissions. |

#### LOOP-P1-07 — Meeting watchdog settings not seeded (soft-fail via code defaults)
| Field | Detail |
| --- | --- |
| **Severity** | P1 (ops/correctness) |
| **Area** | Meeting orchestrator / config |
| **Evidence** | `meeting_watchdog.py` reads `meeting_watchdog_check_interval_seconds`, `meeting_invite_accept_timeout_seconds`, `meeting_invite_arrival_timeout_seconds` via `config.get_*` with `or` fallbacks. These keys are **absent** from `_SEED_SETTINGS` in `db/settings.py`. |
| **Impact** | Settings UI cannot tune timeouts; behavior relies on hidden code defaults; risk of drift vs documented config philosophy (“no hardcoded defaults”). |
| **Fix direction** | Seed the keys; surface in Settings; add tests. |

#### TEST-P1-08 — Test gaps hide P0s
| Field | Detail |
| --- | --- |
| **Severity** | P1 |
| **Area** | Tests |
| **Evidence** | Tree only has `tests/test_meeting_orchestrator.py` (+ conftest). No tests for `_check_auth`, policy seed dangerous commands, path jail, secret redaction, claim leases, or shell absolute-path denial. |
| **Impact** | Regressions in SEC-P0-* can ship unnoticed (as with “loop bugfixes” focusing elsewhere). |
| **Fix direction** | Add a minimal security regression suite before enabling shell/Telegram in prod-like configs. |

---

## Checked — no P0 found (with notes)

| Area | Result |
| --- | --- |
| Virtual FS traversal (`virtual_fs.py`, `filesystem.resolve_relative_path`) | **OK** — rejects escapes outside mount roots. |
| Classic shell injection (`shell=True`, `os.system`, `eval`, `pickle`) | **Not present** in bm_cli executor; uses `shlex.split` + `shell=False`. |
| Git subprocess | Args passed as list (`workspace_git._run_git`); low injection risk (watch leading `-` revisions — deferred P2). |
| DB init | `CREATE IF NOT EXISTS` + additive migrations; table rebuilds disable FK briefly — acceptable for local SQLite; backup-before-reset is good. |
| LLM client | Does not log API keys; logs model/api_base/extra_body only. |
| Template engine | Constrained; no arbitrary expression eval. |

---

## Out of scope / P2+

- Broad refactors of agent loop / meeting UX.
- Full multi-tenant remote hosting hardening (product is desktop-first).
- Git revision/`--` option injection hardening.
- `PYTHONPATH` in shell safe-env allowlist.
- Desktop `pkill -f main.py` possibly matching unrelated processes.
- Prompt-history token budgeting edge cases.
- Expanding personality/prompt content quality.
- Migrating off SQLite.

---

## Recommended next 3 fixes

1. **SEC-P0-01 + SEC-P0-02 (trust boundaries):** Fail-closed Telegram allowlist; redact all secret API responses; add a minimal local auth token for REST/WS; gate reseed/simulator/approve routes.
2. **SEC-P0-03 + SEC-P1-04 (shell):** Before recommending shell to users, remove interpreters/`xargs` from `always_allowed`, add `bash`/`sh` to `never_allowed`, and enforce workspace path confinement on argv paths.
3. **SEC-P0-04 + TEST-P1-08 (blast radius + visibility):** Narrow company files root to `artifacts/projects`; relocate `db_backups`; add regression tests for Telegram deny-by-default, secret redaction, and shell absolute-path rejection.