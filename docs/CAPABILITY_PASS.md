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

## Live scenario

See the pull request body for the reproducible API + CLI executor run (fixture file, read/edit, denied `/etc/passwd`, `uname -a`, path-escape jail). A true interactive GUI agent loop was not required for that proof.
