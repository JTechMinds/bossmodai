# BossMod AI — Day-one capability pass (host file + CLI)

**Scope:** Jordan’s (1) and (2) only. Peer assign/deliver is **out of scope** and not claimed here.

**Honesty:** “My PC projects” is still `artifacts/projects` plus optional **allowlisted extra host roots**. This is not a full unrestricted host mount. A path the user names works when it stays inside `/me`, `/projects`, or a configured host root. Paths outside those roots fail with a clear denial. `cli_shell_enabled` stays fail-closed (`false`). Approval does not bypass the path jail.

## What shipped

1. **Named path open/read/edit.** `resolve_cli_path`, Company Files, and agent `cli` (`cat` / `write` / …) accept a user-named absolute path when it resolves under:
   - the agent workspace (`/me`)
   - the shared projects mount (`/projects` → `artifacts/projects`)
   - extra directories in Settings → CLI Policy → **Host workspace roots** (`workspace_host_roots`)
2. **Diagnostic CLI.** With shell enabled, pathless diagnostics such as `uname -a` run under the existing jail/policy. `ls` / `cat` of an allowed path work. `cat /etc/passwd` (and other escapes) are denied by virtual-path confinement or the argv path jail. Approval still does not jailbreak.

## What did not ship

- A bind-mount of the operator’s whole home directory or an unnamed “open any host path” mode
- Peer collaboration / assign-and-deliver (Jordan’s (3))
- A live multi-agent GUI loop in this verification environment (contracts are proven via the same API + `execute_bm_cli` / company-files paths the live loop calls)

## Operator setup

1. Settings → CLI Policy → Host workspace roots: one absolute directory per line (must exist).
2. Rejected as extra roots: `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/root`.
3. Empty setting = no extra host access (default).
4. Settings → CLI Policy → Shell Executor stays off until you want native diagnostics (`uname`, `ls` of an allowed path, …). Virtual `cat` / `write` of an allowed named path do not require shell.

## Live scenario (reproducible)

A true interactive GUI agent loop was **not** run. The same contracts the live loop calls were exercised against a live FastAPI process (`uv run python main.py`) with `X-BossMod-Token`.

```bash
# fixture
mkdir -p /tmp/bossmod-cap-host
printf '%s\n' '# fixture named by the operator' 'print("before-review")' > /tmp/bossmod-cap-host/review.py

# server
BOSSMOD_DB_PATH=/tmp/bossmod-cap/bossmod.sqlite3
BOSSMOD_LOCAL_API_TOKEN=cap-token-c3b7
BOSSMOD_HOST=127.0.0.1
BOSSMOD_PORT=38472
# then: PUT workspace_host_roots=/tmp/bossmod-cap-host
#       PUT cli_shell_enabled=true   # opt-in for native uname/head only; seed default stays false
#       POST /api/agents  {"name":"Hugh Proof","role":"Reviewer"}
```

Captured on this pass (`ed5f9ab`, 2026-09-05T20:49:31Z):

| Step | Call | Result |
| --- | --- | --- |
| A read | `GET /api/company/files?path=/tmp/bossmod-cap-host/review.py` | **200**; content `print("before-review")` |
| A edit | `PUT /api/company/files` same path | **200**; file became `print("after-review")` |
| A CLI | `POST /api/cli-policy/simulator/execute` `cat` / `write` `execute=true` | **200** `ok=true` `exit_code=0` `executor=virtual` |
| A deny | `GET /api/company/files?path=/etc/passwd` | **400** outside allowed roots; not a full host mount |
| B ok | `uname -a` via simulator `execute=true` | **200** `ok=true` `exit_code=0` `executor=shell`; stdout starts `Linux cursor 6.12.94+` |
| B ok | `ls /tmp/bossmod-cap-host` | **200** `ok=true`; listing `review.py` |
| B deny | `head /etc/passwd` (native, not virtual `cat`) | **200** `ok=false` `exit_code=1` `executor=shell`; `Path jail: '/etc/passwd' resolves outside the allowed workspace roots` |

Virtual `cat /etc/passwd` is denied by named-path confinement (`ok=false`, `executor=virtual`) before the shell jail. Full transcripts are in the pull request body.
