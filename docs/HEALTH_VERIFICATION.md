# BossMod AI — Health verification pass

**Date:** 2026-09-05  
**Tree:** `main` @ `95a58ce` (`HA-OPS-P1-01 + remaining small health follow-ups`, PR [#20](https://github.com/JTechMinds/bossmodai/pull/20))  
**Goal:** Prove HEALTH_BACKLOG “shipped” claims still hold. No STRUCT peels or new product work.

**Result:** **PASS.** `uv run pytest -q` is green (288 passed). Live FastAPI + static UI smoke matches the shipped security/ops claims. Tauri desktop could not be launched in this environment (missing `webkit2gtk`). No functional regressions found.

---

## 1. What was verified

| Claim | How | Outcome |
| --- | --- | --- |
| Critical-path pytest suite is green | `uv run pytest -q` | **288 passed**, 0 failed, 11.02s |
| PR #2 token gate + redaction is on `main` | Living-doc lint + live HTTP | REST `/api/*` without token → 401; WS without token → 403; `/health` and `/` stay open |
| Settings / connections redact secrets | `GET /api/settings`, `GET /api/connections` after write | Telegram token `has_value` + last-4 `wxyz`; connection `has_api_key` + last-4 `abcd`; raw secrets absent |
| Company files root hides backups | `GET /api/company/files?path=/` + raw paths + UI Files tab | Root lists `verify-alpha` only; `/db_backups/…` and `/agents/…` → 404 |
| Secrets at rest are `bm1:` blobs | SQLite dump of secret columns | `telegram_bot_token` / `local_api_token` stored as `bm1:…`; plaintext not in the DB file |
| Connection-test URL allowlist | `POST /api/connections/test` `http://169.254.169.254/` | `{ok: false}` before fetch |
| First-run no-model banner | Static UI at `/` with empty connections | Banner + “Connect a model in Settings”; Send stays disabled |
| Offline UI chrome | `GET /` HTML | Vendored Tailwind/Lucide/Split; no `cdn.tailwindcss.com` / `unpkg.com` |
| Shared JS API client | `rg "fetch\\('/api" ui/static/js` | No raw `fetch('/api` outside the helper |
| `core/` does not import `api` | `rg "from api\\.|import api" core` | Clean |
| Desktop no longer `pkill -f` | Source contract (`desktop/src/main.rs`) | `pkill` only in a comment; recorded PID + cmdline check present |
| Unused `duckdb` / `twilio` | `pyproject.toml` + README | Absent |
| Living docs do not claim PR #2 is open | `ARCHITECTURE.md`, `HEALTH_BACKLOG.md` | PR #2 described as merged; this pass also fixed two leftover stale sentences (see findings) |

---

## 2. Evidence

### 2.1 Pytest

```text
$ git rev-parse HEAD
95a58ce901ea8d9a99e0b1af1cb58e59aa4cf44e

$ uv run pytest -q
........................................................................ [ 25%]
........................................................................ [ 50%]
........................................................................ [ 75%]
........................................................................ [100%]
288 passed in 11.02s
```

Environment note: this VM did not have `uv` on `PATH`. `uv 0.12.10` was installed to `$HOME/.local/bin`, then `uv sync` created `.venv` against CPython 3.12.3. That is an environment setup step, not a product defect.

### 2.2 Live FastAPI smoke

Backend:

```text
BOSSMOD_DB_PATH=/tmp/bossmod-verify/bossmod.sqlite3
BOSSMOD_LOCAL_API_TOKEN=verify-token-c788
BOSSMOD_HOST=127.0.0.1
BOSSMOD_PORT=38471
uv run python main.py
# Application startup complete.
# Uvicorn running on http://127.0.0.1:38471
```

Seeded on disk for the company-files check (not served as the browser root):

- `artifacts/projects/verify-alpha/notes.md`
- `artifacts/db_backups/bossmod.sqlite3.20260101T000000Z.bak`
- `artifacts/agents/agent_0001/secret.md`

| Request | Auth | Result |
| --- | --- | --- |
| `GET /health` | none | 200 `{"status":"ok","version":"0.1.0"}` |
| `GET /api/connections` | none | 401 `Missing or invalid local API token` |
| `POST /api/settings/reseed` | none | 401 |
| `GET /api/ws` (websocket upgrade, no token) | none | 403 |
| `GET /api/connections` | `X-BossMod-Token` | 200 `[]` (after deleting the smoke connection) |
| `PUT /api/settings/telegram_bot_token?value=123456:TELEGRAM-SECRET-wxyz` | token | 200; `value=""`, `has_value=true`, `value_last4=wxyz` |
| `GET /api/settings` | token | raw `TELEGRAM-SECRET` absent; `local_api_token` omitted |
| `POST /api/connections` with `sk-test-SECRETVALUE-abcd` | token | 201; `has_api_key=true`, `api_key_last4=abcd`; raw key absent |
| `GET /api/company/files?path=/` | token | 200; entries = `verify-alpha` only; no `db_backups` / `agent_0001` |
| `GET /api/company/files/raw?path=/db_backups/…` | token | 404 `File not found` |
| `GET /api/company/files/raw?path=/agents/agent_0001/secret.md` | token | 404 |
| `GET /api/company/files/raw?path=/verify-alpha/notes.md` | token | 200 `hello from company files root` |
| `POST /api/connections/test` `http://169.254.169.254/` | token | 200 `{ok:false, error:"http is only allowed for loopback…"}` |
| `GET /` | none | 200 HTML; token injected in `<meta name="bossmod-api-token">`; vendored Tailwind; `#no-model-banner` present |

SQLite at-rest check (`/tmp/bossmod-verify/bossmod.sqlite3`):

```text
telegram_bot_token  bm1:O6xLofVcIla-…   (len 104)
local_api_token     bm1:s6Fb-ZJJ1d5K…   (len 128)
TELEGRAM-SECRET in file: False
sk-test-SECRETVALUE in file: False
```

### 2.3 Static UI smoke (FastAPI + Chrome)

Tauri was not used. Chrome opened `http://127.0.0.1:38471/`.

Observed:

- Office map loads; footer shows **Connected**, 0 agents.
- Yellow banner: “No AI model is connected. Connect a model in Settings — chat Send stays disabled until a connection exists.”
- Company → Files lists only `verify-alpha/` (“1 folder, 0 files”). No `db_backups`, no raw `agents`.
- Settings → AI Connections: “No connections yet.”
- Settings → Telegram: bot token shown as `****wxyz` with copy “full value is never shown after save.” Allowed-user-IDs `123456789, 987654321` is the **placeholder**, not a stored allowlist.

---

## 3. Gaps (could not test)

| Gap | Why | Severity of the gap |
| --- | --- | --- |
| **Tauri desktop / `./run.sh`** | `webkit2gtk-4.1` / `4.0` not installed; no `libwebkit` libs. `DISPLAY=:1` exists, but the webview cannot link. A full `cargo build --release` was not started. | Environment gap, not a product fail. HA-OPS-P2-02 “second launch does not pkill strangers” remains source-only (same as PR #20). |
| Live Telegram bot | No bot token / network allowlist exercise beyond fail-closed copy and settings redaction. | Expected. |
| Live LLM turn | No provider key; first-run banner / skip-turn covered by tests + UI, not a real completion. | Expected. |
| WebSocket happy path with `?token=` | Only the unauthenticated 403 was hit live. Authenticated WS is covered by existing tests / UI “Connected” footer. | Minor. |
| Image preview `apiFetchBlobUrl` | No image file was opened in Company Files. Source lint still asserts no bare `<img src="/api/…">`. | Minor. |
| Simulator **Execute for real** | Not clicked in the UI. Default dry-run remains pytest-covered. | Minor. |
| Multi-user / `BOSSMOD_HOST=0.0.0.0` | Out of scope (explicitly not multi-tenant). | Documented residual. |

---

## 4. Living-doc spot-check

Checked as living status: `docs/ARCHITECTURE.md`, `docs/HEALTH_BACKLOG.md`.  
`docs/HEALTH_AUDIT.md` and `docs/AUDIT_P0_P1.md` are labeled snapshots of `main` @ `f5405bc` and were **not** rewritten.

**Already honest on tip (PR #20):**

- Backlog header: PR #2 is “live, not open” / “Merged on `main`.”
- Architecture trust diagram: local API token, fail-closed Telegram, path jail, `artifacts/projects` root.
- Every HA-* acceptance checkbox is `[x]` with a **Shipped** section. `rg -- '- \\[ \\]' docs/HEALTH_BACKLOG.md` is empty.

**Stale lines found and corrected in this PR (docs only):**

1. `ARCHITECTURE.md` still said `api/` was “One 2.3k-LOC router” after HA-STRUCT-P1-01. Actual tree: split routers, `wc -l api/routes/*.py` = 2555 total, `agents.py` 808.
2. `ARCHITECTURE.md` residual sentence said “see HEALTH_BACKLOG for remaining open items,” and the glance table still read like a todo list even though every item is shipped.

Problem statements inside backlog items still describe the **old** bug in present tense. That is backlog style; each item’s **Shipped** section is the current status. Not treated as a false security claim.

---

## 5. New findings

No P0/P1 functional or security regressions on `95a58ce`.

| ID | Sev | Finding | Action |
| --- | --- | --- | --- |
| HV-DOC-01 | P3 (docs) | Living `ARCHITECTURE.md` still described the pre-split 2.3k-LOC router and an open backlog. | Fixed in this PR; linted in `tests/test_health_ops_ui.py`. |
| HV-DOC-02 | P3 (docs) | Glance table in `HEALTH_BACKLOG.md` had no “all shipped” banner, so a skimmer could treat the sequence as open work. | One-line status banner added. |
| HV-ENV-01 | — | Cloud agent image has no `webkit2gtk`; Tauri/`./run.sh` cannot be proven here. | Gap only. |

---

## 6. Verdict

HEALTH_BACKLOG shipped claims **still hold** on current `main` (`95a58ce` / PR #20):

- Auth, redaction, company-files jail, secrets at rest, connection-test allowlist, no-model banner, vendored UI, and the pytest suite all reproduced.
- Remaining work in that file is **out of scope** (hosted hardening, landlock, prompt tone, etc.), not unshipped HA-* items.
- Desktop shell remains unproven in this environment; use the FastAPI + static UI path or a machine with WebKitGTK to confirm Tauri PID cleanup live.
