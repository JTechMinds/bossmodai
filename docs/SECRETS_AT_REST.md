# Secrets at rest (HA-SEC-P1-05)

## Decision

BossMod is a single-operator desktop app. The SQLite file and a sibling
data key live on the same machine. Full-disk encryption (OS / FileVault /
LUKS) is still the control against theft of the whole data directory.

This PR adds a **second, narrower control**: secret *columns* are wrapped
so a copied database file is not plaintext. That matters for
`artifacts/db_backups/` snapshots (HA-SEC-P0-04 already hid them from the
company file browser) and for anyone who copies `bossmod.sqlite3` without
the key file.

| Choice | Why |
| --- | --- |
| File key, not OS keychain | Linux keychain UX is messy for a local-first desktop app; a `chmod 600` file next to the DB is enough for this threat model. |
| Wrap three surfaces only | `ai_connections.api_key`, `agents.api_key`, and secret settings (`telegram_bot_token`, `local_api_token`). Other settings are operational config, not credentials. |
| Stdlib wrap (`bm1:`) | Avoid a new crypto dependency. Encrypt-then-MAC (HMAC-SHA256) over a SHA-256 keystream. Not a substitute for AES-GCM in a multi-tenant host. |
| Transparent CRUD | Application code still sees plaintext. `GET` redaction from PR #2 is unchanged. |

## Key file

Path: `{dirname(BOSSMOD_DB_PATH)}/.bossmod_data_key` (32 random bytes, mode `0600`).
Created on first wrap. Gitignored. Not copied by `reset_database()` backups.

Losing the key makes wrapped columns unreadable. Back up the key with the same
care as the API tokens it protects.

## What this does not do

- Encrypt the whole SQLite file or WAL.
- Hide secrets from a process that can read both the DB and the key.
- Replace localhost API redaction (`GET` still never returns raw keys).
- Use the OS keychain (deferred; revisit if we ship a hosted multi-user build).
