"""BossMod AI — SQLite connection management.

Provides per-thread SQLite connections to the project database file and
initialises the schema on first access.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = os.environ.get("BOSSMOD_DB_PATH", str(_PROJECT_ROOT / "bossmod.sqlite3"))


def database_path() -> Path:
    """Return the configured SQLite file path."""
    return Path(_DB_PATH)
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_SHOW_TABLES_SQL = """
SELECT name
FROM sqlite_master
WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
ORDER BY name
"""
_SQL_CAST_RE = re.compile(r"::[A-Za-z_][A-Za-z0-9_]*")
_SQL_PARAM_RE = re.compile(r"\$(\d+)\b")
_SQLITE_HEADER = b"SQLite format 3\x00"

_connection_lock = threading.RLock()
_thread_local = threading.local()
_thread_connections: dict[int, "SQLiteCompatConnection"] = {}


def _adapt_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat(sep=" ")
    return value.astimezone(timezone.utc).isoformat(sep=" ")


def _convert_timestamp(raw: bytes) -> datetime:
    value = datetime.fromisoformat(raw.decode())
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("BOOLEAN", lambda raw: bool(int(raw.decode())))
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)


class SQLiteCompatConnection:
    """Small compatibility wrapper around sqlite3 for the existing DB layer."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None):
        normalized_sql, normalized_params = _normalize_statement(sql, params)
        if normalized_params is None:
            return self._raw.execute(normalized_sql)
        return self._raw.execute(normalized_sql, normalized_params)

    def executescript(self, sql: str):
        return self._raw.executescript(sql)

    def interrupt(self) -> None:
        self._raw.interrupt()

    def close(self) -> None:
        self._raw.close()


def _utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_statement(
    sql: str,
    params: list[Any] | tuple[Any, ...] | dict[str, Any] | None,
) -> tuple[str, list[Any] | tuple[Any, ...] | dict[str, Any] | None]:
    normalized = sql.strip()
    if normalized.rstrip(";").upper() == "SHOW TABLES":
        return _SHOW_TABLES_SQL, None

    normalized = _SQL_CAST_RE.sub("", normalized)
    normalized = re.sub(r"\bILIKE\b", "LIKE", normalized, flags=re.IGNORECASE)

    if params is None:
        return normalized, None
    if isinstance(params, dict):
        return normalized, params
    if not params:
        return normalized, None
    if not _SQL_PARAM_RE.search(normalized):
        return normalized, params

    bound = {f"p{index + 1}": value for index, value in enumerate(params)}
    rewritten = _SQL_PARAM_RE.sub(lambda match: f":p{match.group(1)}", normalized)
    return rewritten, bound


def _close_safely(con: SQLiteCompatConnection | None) -> None:
    if con is None:
        return
    try:
        con.close()
    except Exception:
        pass


def _invalidate_all_thread_connections_unlocked() -> None:
    for ident, con in list(_thread_connections.items()):
        _close_safely(con)
        _thread_connections.pop(ident, None)
    if hasattr(_thread_local, "connection"):
        _thread_local.connection = None


def _create_raw_connection() -> sqlite3.Connection:
    db_path = Path(_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and db_path.stat().st_size > 0:
        header = db_path.read_bytes()[: len(_SQLITE_HEADER)]
        if header != _SQLITE_HEADER:
            raise RuntimeError(
                f"Configured database file is not an SQLite database: {db_path}. "
                "Use BOSSMOD_DB_PATH to point at a fresh SQLite file, or remove the old path."
            )
    raw = sqlite3.connect(
        db_path,
        timeout=30.0,
        isolation_level=None,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA synchronous = NORMAL")
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA busy_timeout = 5000")
    raw.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
    raw.create_function("now", 0, _utc_now_sql)
    return raw


def get_connection() -> SQLiteCompatConnection:
    """Return a thread-local SQLite connection wrapper."""
    con = getattr(_thread_local, "connection", None)
    if con is not None:
        try:
            con.execute("SELECT 1")
        except Exception:
            logger.warning("SQLite thread connection health check failed — recreating")
            close_thread_connection()
            con = None

    if con is None:
        con = SQLiteCompatConnection(_create_raw_connection())
        with _connection_lock:
            _thread_local.connection = con
            _thread_connections[threading.get_ident()] = con
    return con


@contextmanager
def transaction() -> Generator[SQLiteCompatConnection, None, None]:
    """Wrap a block in BEGIN / COMMIT / ROLLBACK."""
    con = get_connection()
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _apply_schema(con: SQLiteCompatConnection) -> None:
    """Execute the DDL in schema.sql to ensure all tables exist."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.executescript(sql)
    logger.info("Schema applied from %s", _SCHEMA_PATH)


def _apply_migrations(con: SQLiteCompatConnection) -> None:
    """Apply additive column migrations for existing databases."""
    _ensure_source_keyed_sticky_slots(con)
    _create_task_events_table_if_missing(con)
    _ensure_agent_state_status_values(con)
    _ensure_task_status_values(con)
    _create_host_path_consent_tables_if_missing(con)
    _ensure_workspace_preference_consent_schema(con)
    _ensure_shell_executor_consent_schema(con)
    _ensure_nest_git_consent_schema(con)
    _ensure_notification_kind_values(con)
    _ensure_notification_link_target_kinds(con)
    _ensure_meeting_host_nullable(con)
    _ensure_cli_approval_origin_schema(con)
    _add_column_if_missing(
        con, "agent_triggers", "retry_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        con, "tasks", "requester_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "tasks", "owner_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "bm_cli_events", "approval_request_id",
        "VARCHAR REFERENCES cli_approval_requests(id)",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "category",
        "VARCHAR NOT NULL DEFAULT 'general'",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "usage_syntax", "VARCHAR",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "help_text", "TEXT",
    )
    _add_column_if_missing(
        con, "agent_triggers", "claim_generation",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        con, "agent_triggers", "claim_lease", "VARCHAR",
    )
    _add_column_if_missing(
        con, "agents", "done_fail_bar", "TEXT",
    )
    _add_column_if_missing(
        con, "agents", "communication", "TEXT",
    )
    _add_column_if_missing(
        con, "agent_templates", "communication", "TEXT",
    )
    # After the column above, never before it: the rebuild copies every
    # column by name, `communication` included, and a library older than that
    # column would otherwise fail the copy.
    _ensure_agent_template_local_source(con)
    _add_column_if_missing(
        con, "agents", "description", "TEXT",
    )
    _add_column_if_missing(
        con, "channel_messages", "notification_kind", "VARCHAR",
    )
    _add_column_if_missing(
        con, "channel_messages", "consent_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "channel_messages", "desk_path", "VARCHAR",
    )
    _add_column_if_missing(
        con, "channel_messages", "task_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "host_path_consent_requests", "channel_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "cli_approval_requests", "channel_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "channel_messages", "approval_id", "VARCHAR",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "cwd_prefix", "VARCHAR",
    )
    _add_column_if_missing(
        con, "tasks", "closed_at", "TIMESTAMP",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "round_index",
        "INTEGER NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "dispatch_mode",
        "VARCHAR NOT NULL DEFAULT 'fanout'",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "stepped_out",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "next_mentions",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "router_mode",
        "VARCHAR NOT NULL DEFAULT 'fallback'",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "pinned_ids",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        con, "channel_response_rounds", "work_bind_ids",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        con, "channels", "cli_auto_approve",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_floors(con)
    _add_column_if_missing(
        con, "cli_approval_requests", "review_note", "TEXT",
    )
    _backfill_task_closed_at(con)
    _create_work_snapshots_table_if_missing(con)
    _raise_default_no_progress_threshold(con)
    # Last, and before init_db backfills missing identities: a backfill
    # allocates from this ledger, so the ledger must already know every key
    # that was issued or it would hand one out again.
    _seed_agent_storage_keys(con)


_STORAGE_KEY_NAME_RE = re.compile(r"^agent_(\d+)$")


def _storage_index_in_name(name: str) -> int | None:
    """Return ``NNNN`` from an ``agent_NNNN`` name, or None for any other name."""
    match = _STORAGE_KEY_NAME_RE.match(name)
    return int(match.group(1)) if match else None


def _seed_agent_storage_keys(con: SQLiteCompatConnection) -> None:
    """Teach the storage-key ledger every key already issued, from rows and from disk.

    1. Each ``agent_storage_identities`` row not yet in ``agent_storage_keys``
       is copied there with the same index and key, ``retired_at`` NULL.
    2. The ledger's AUTOINCREMENT high-water mark (``sqlite_sequence``) is
       raised to the highest ``NNNN`` among the ``agent_NNNN`` folders under
       the agents root and the ``agent_NNNN.json`` files under the standing
       prefs root. A key whose files survived an old delete (before the
       ledger existed) is then never issued again, so no new hire inherits
       those files.

    Idempotent: keys already in the ledger are skipped and the high-water
    mark is only ever raised. Raises ``sqlite3.IntegrityError`` when an
    identity's index is already in the ledger under a different key, a state
    the ledger cannot have produced; ``OSError`` when a root cannot be listed.
    """
    # Imported here, not at module top: db must be importable before core.
    from core.bm_cli.filesystem import agents_artifact_root, standing_prefs_root

    con.execute(
        """
        INSERT INTO agent_storage_keys (storage_index, storage_key, agent_id)
        SELECT storage_index, storage_key, agent_id
        FROM agent_storage_identities
        WHERE storage_key NOT IN (SELECT storage_key FROM agent_storage_keys)
        """
    )
    on_disk = [
        _storage_index_in_name(entry.name)
        for entry in agents_artifact_root().iterdir()
        if entry.is_dir()
    ] + [
        _storage_index_in_name(entry.stem)
        for entry in standing_prefs_root().iterdir()
        if entry.is_file() and entry.suffix == ".json"
    ]
    highest = max((index for index in on_disk if index is not None), default=0)
    if highest == 0:
        return
    # sqlite_sequence has no row for a table AUTOINCREMENT has never issued
    # from, and SQLite reads a missing row as 0.
    con.execute(
        """
        INSERT INTO sqlite_sequence (name, seq)
        SELECT 'agent_storage_keys', 0
        WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'agent_storage_keys')
        """
    )
    con.execute(
        "UPDATE sqlite_sequence SET seq = $1 WHERE name = 'agent_storage_keys' AND seq < $1",
        [highest],
    )


def _create_work_snapshots_table_if_missing(con: SQLiteCompatConnection) -> None:
    """Backfill the frozen work-transcript table for existing databases."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS work_snapshots (
            activity_id              VARCHAR PRIMARY KEY REFERENCES activities(id) ON DELETE CASCADE,
            agent_id                 VARCHAR NOT NULL,
            task_id                  VARCHAR,
            transcript               TEXT NOT NULL DEFAULT '[]',
            fingerprints             TEXT NOT NULL DEFAULT '[]',
            interludes               TEXT NOT NULL DEFAULT '[]',
            no_progress_checkpoints  INTEGER NOT NULL DEFAULT 0,
            created_at               TIMESTAMP DEFAULT current_timestamp,
            updated_at               TIMESTAMP DEFAULT current_timestamp
        )
        """
    )


# The shipped per-agent no-progress default before it was raised to 100.
_FACTORY_NO_PROGRESS_THRESHOLD = 30
_DEFAULT_NO_PROGRESS_THRESHOLD = 100


def _raise_default_no_progress_threshold(con: SQLiteCompatConnection) -> None:
    """Move agents still on the old factory no-progress threshold to 100.

    The column is not exposed in the UI or API, so 30 can only be the old
    default, never an operator choice. Idempotent: rows already at another
    value are left alone.
    """
    con.execute(
        "UPDATE agents SET guardian_no_progress_threshold = $1 "
        "WHERE guardian_no_progress_threshold = $2",
        [_DEFAULT_NO_PROGRESS_THRESHOLD, _FACTORY_NO_PROGRESS_THRESHOLD],
    )


def _ensure_source_keyed_sticky_slots(con: SQLiteCompatConnection) -> None:
    """Replace a conversation-scoped pocket with source-keyed task slots.

    The earlier shape stored one row per chat scope. Slots are keyed by
    an existing task, owner, verdict path, or blocker id. A database that
    already has that key is left untouched, so open rows are not wiped.
    """
    result = con.execute("PRAGMA table_info(sticky_slots)")
    columns = {row[1] for row in result.fetchall()}
    if "source_id" in columns and "slot_kind" in columns:
        return
    con.execute("DROP TABLE IF EXISTS sticky_slots")
    con.execute(
        """
        CREATE TABLE sticky_slots (
            source_id   VARCHAR NOT NULL,
            slot_kind   VARCHAR NOT NULL
                            CHECK (slot_kind IN ('plan', 'next_owner', 'verdict_path', 'blockers')),
            body        TEXT NOT NULL,
            updated_at  TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (source_id, slot_kind)
        )
        """
    )


def _add_column_if_missing(
    con: SQLiteCompatConnection, table: str, column: str, definition: str,
) -> None:
    """Add a column to an existing table if it doesn't already exist."""
    result = con.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in result.fetchall()}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Migration: added column %s.%s", table, column)


def _ensure_floors(con: SQLiteCompatConnection) -> None:
    """Create Lobby and give every existing agent and thread that home.

    Rows that already name a floor are left alone. Only missing homes are
    filled, so a later boot does not move anyone and does not wipe work. An
    agent on vacation (``vacation_since`` set) has no floor by design and is
    never filled.
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS floors (
            id          VARCHAR PRIMARY KEY,
            name        VARCHAR NOT NULL UNIQUE,
            created_at  TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    con.execute(
        "INSERT INTO floors (id, name) VALUES ($1, $2) ON CONFLICT(id) DO NOTHING",
        ["lobby", "Lobby"],
    )
    _add_column_if_missing(con, "agents", "floor_id", "VARCHAR")
    _add_column_if_missing(con, "channels", "floor_id", "VARCHAR")
    # Before the backfill, never after it: an agent on vacation has no floor
    # on purpose, and a backfill that could not see the column would pull
    # every vacationer back into Lobby on each boot.
    _add_column_if_missing(con, "agents", "vacation_since", "TIMESTAMP")
    con.execute(
        "UPDATE agents SET floor_id = $1 "
        "WHERE (floor_id IS NULL OR floor_id = '') AND vacation_since IS NULL",
        ["lobby"],
    )
    con.execute(
        "UPDATE channels SET floor_id = $1 WHERE floor_id IS NULL OR floor_id = ''",
        ["lobby"],
    )


def _backfill_task_closed_at(con: SQLiteCompatConnection) -> None:
    """Give every finished task a finish time.

    Rows that ended before ``closed_at`` existed carry NULL. ``last_activity``
    is the nearest recorded time to their finish, and it is the time the
    operator was shown for them until now. Only NULLs are written, so this is
    idempotent and never rewrites a stamp ``update_task`` made.
    """
    # Imported here, not at module top: db must be importable before core.tasking.
    from core.tasking.transitions import TERMINAL_TASK_STATUSES

    statuses = sorted(TERMINAL_TASK_STATUSES)
    placeholders = ", ".join(f"${index}" for index in range(1, len(statuses) + 1))
    con.execute(
        "UPDATE tasks SET closed_at = last_activity "
        f"WHERE closed_at IS NULL AND status IN ({placeholders})",
        statuses,
    )


def _create_host_path_consent_tables_if_missing(con: SQLiteCompatConnection) -> None:
    """Backfill in-chat host-path consent tables for existing databases."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS host_path_consent_requests (
            id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
            agent_id        VARCHAR NOT NULL REFERENCES agents(id),
            path            VARCHAR NOT NULL,
            grant_root      VARCHAR NOT NULL,
            reason          TEXT NOT NULL,
            command         TEXT,
            content         TEXT,
            cwd             VARCHAR,
            task_id         VARCHAR REFERENCES tasks(id),
            channel_id      VARCHAR,
            card_kind       VARCHAR NOT NULL DEFAULT 'host_path'
                                CHECK (card_kind IN (
                                    'host_path', 'workspace_preference', 'shell_executor', 'nest_git'
                                )),
            is_git          BOOLEAN NOT NULL DEFAULT FALSE,
            clone_dest      VARCHAR,
            status          VARCHAR NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'allowed_once', 'always_allowed', 'denied',
                                    'cloned', 'branched', 'edit_host', 'enabled'
                                )),
            decision_by     VARCHAR,
            decision_note   TEXT,
            decided_at      TIMESTAMP,
            expires_at      TIMESTAMP,
            created_at      TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS host_path_once_grants (
            id          VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
            agent_id    VARCHAR NOT NULL REFERENCES agents(id),
            root        VARCHAR NOT NULL,
            consent_id  VARCHAR NOT NULL REFERENCES host_path_consent_requests(id),
            task_id     VARCHAR REFERENCES tasks(id),
            created_at  TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_host_path_consent_agent_status
            ON host_path_consent_requests (agent_id, status, created_at)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_host_path_consent_path
            ON host_path_consent_requests (agent_id, path, status)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_host_path_once_grants_agent
            ON host_path_once_grants (agent_id, task_id)
        """
        )


def _ensure_workspace_preference_consent_schema(con: SQLiteCompatConnection) -> None:
    """Add workspace-preference columns and expand consent status values."""
    _add_column_if_missing(
        con, "host_path_consent_requests", "card_kind",
        "VARCHAR NOT NULL DEFAULT 'host_path'",
    )
    _add_column_if_missing(
        con, "host_path_consent_requests", "is_git",
        "BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        con, "host_path_consent_requests", "clone_dest", "VARCHAR",
    )
    sql = _table_sql(con, "host_path_consent_requests")
    if "'cloned'" in sql and "'workspace_preference'" in sql:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS host_path_consent_requests__new (
                id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                agent_id        VARCHAR NOT NULL REFERENCES agents(id),
                path            VARCHAR NOT NULL,
                grant_root      VARCHAR NOT NULL,
                reason          TEXT NOT NULL,
                command         TEXT,
                content         TEXT,
                cwd             VARCHAR,
                task_id         VARCHAR REFERENCES tasks(id),
                channel_id      VARCHAR,
                card_kind       VARCHAR NOT NULL DEFAULT 'host_path'
                                    CHECK (card_kind IN ('host_path', 'workspace_preference')),
                is_git          BOOLEAN NOT NULL DEFAULT FALSE,
                clone_dest      VARCHAR,
                status          VARCHAR NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'allowed_once', 'always_allowed', 'denied',
                                        'cloned', 'branched', 'edit_host'
                                    )),
                decision_by     VARCHAR,
                decision_note   TEXT,
                decided_at      TIMESTAMP,
                expires_at      TIMESTAMP,
                created_at      TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO host_path_consent_requests__new (
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id, card_kind, is_git, clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            )
            SELECT
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id,
                COALESCE(card_kind, 'host_path'),
                COALESCE(is_git, 0),
                clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            FROM host_path_consent_requests
            """
        )
        con.execute("DROP TABLE host_path_consent_requests")
        con.execute(
            "ALTER TABLE host_path_consent_requests__new RENAME TO host_path_consent_requests"
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_agent_status
                ON host_path_consent_requests (agent_id, status, created_at)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_path
                ON host_path_consent_requests (agent_id, path, status)
            """
        )
        logger.info("Migration: rebuilt host_path_consent_requests for workspace preference")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_shell_executor_consent_schema(con: SQLiteCompatConnection) -> None:
    """Allow shell-executor consent cards and the enabled status."""
    sql = _table_sql(con, "host_path_consent_requests")
    if "'shell_executor'" in sql and "'enabled'" in sql:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS host_path_consent_requests__new (
                id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                agent_id        VARCHAR NOT NULL REFERENCES agents(id),
                path            VARCHAR NOT NULL,
                grant_root      VARCHAR NOT NULL,
                reason          TEXT NOT NULL,
                command         TEXT,
                content         TEXT,
                cwd             VARCHAR,
                task_id         VARCHAR REFERENCES tasks(id),
                channel_id      VARCHAR,
                card_kind       VARCHAR NOT NULL DEFAULT 'host_path'
                                    CHECK (card_kind IN (
                                        'host_path', 'workspace_preference', 'shell_executor'
                                    )),
                is_git          BOOLEAN NOT NULL DEFAULT FALSE,
                clone_dest      VARCHAR,
                status          VARCHAR NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'allowed_once', 'always_allowed', 'denied',
                                        'cloned', 'branched', 'edit_host', 'enabled'
                                    )),
                decision_by     VARCHAR,
                decision_note   TEXT,
                decided_at      TIMESTAMP,
                expires_at      TIMESTAMP,
                created_at      TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO host_path_consent_requests__new (
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id, card_kind, is_git, clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            )
            SELECT
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id,
                COALESCE(card_kind, 'host_path'),
                COALESCE(is_git, 0),
                clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            FROM host_path_consent_requests
            """
        )
        con.execute("DROP TABLE host_path_consent_requests")
        con.execute(
            "ALTER TABLE host_path_consent_requests__new RENAME TO host_path_consent_requests"
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_agent_status
                ON host_path_consent_requests (agent_id, status, created_at)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_path
                ON host_path_consent_requests (agent_id, path, status)
            """
        )
        logger.info("Migration: rebuilt host_path_consent_requests for shell executor consent")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_nest_git_consent_schema(con: SQLiteCompatConnection) -> None:
    """Allow nest-git consent cards."""
    sql = _table_sql(con, "host_path_consent_requests")
    if "'nest_git'" in sql:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS host_path_consent_requests__new (
                id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                agent_id        VARCHAR NOT NULL REFERENCES agents(id),
                path            VARCHAR NOT NULL,
                grant_root      VARCHAR NOT NULL,
                reason          TEXT NOT NULL,
                command         TEXT,
                content         TEXT,
                cwd             VARCHAR,
                task_id         VARCHAR REFERENCES tasks(id),
                channel_id      VARCHAR,
                card_kind       VARCHAR NOT NULL DEFAULT 'host_path'
                                    CHECK (card_kind IN (
                                        'host_path', 'workspace_preference',
                                        'shell_executor', 'nest_git'
                                    )),
                is_git          BOOLEAN NOT NULL DEFAULT FALSE,
                clone_dest      VARCHAR,
                status          VARCHAR NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                        'pending', 'allowed_once', 'always_allowed', 'denied',
                                        'cloned', 'branched', 'edit_host', 'enabled'
                                    )),
                decision_by     VARCHAR,
                decision_note   TEXT,
                decided_at      TIMESTAMP,
                expires_at      TIMESTAMP,
                created_at      TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO host_path_consent_requests__new (
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id, card_kind, is_git, clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            )
            SELECT
                id, agent_id, path, grant_root, reason, command, content, cwd,
                task_id, channel_id,
                COALESCE(card_kind, 'host_path'),
                COALESCE(is_git, 0),
                clone_dest, status,
                decision_by, decision_note, decided_at, expires_at, created_at
            FROM host_path_consent_requests
            """
        )
        con.execute("DROP TABLE host_path_consent_requests")
        con.execute(
            "ALTER TABLE host_path_consent_requests__new RENAME TO host_path_consent_requests"
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_agent_status
                ON host_path_consent_requests (agent_id, status, created_at)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_host_path_consent_path
                ON host_path_consent_requests (agent_id, path, status)
            """
        )
        logger.info("Migration: rebuilt host_path_consent_requests for nest git consent")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_notification_kind_values(con: SQLiteCompatConnection) -> None:
    """Rebuild notifications if it is missing a later kind value."""
    sql = _table_sql(con, "notifications")
    if "'queue_visibility'" in sql:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications__new (
                id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                agent_id          VARCHAR NOT NULL REFERENCES agents(id),
                task_id           VARCHAR REFERENCES tasks(id),
                activity_id       VARCHAR REFERENCES activities(id),
                kind              VARCHAR NOT NULL
                                     CHECK (kind IN ('receipt', 'completion', 'blocked', 'handoff', 'abandoned', 'task_update', 'host_path_consent', 'queue_visibility')),
                content           TEXT NOT NULL,
                source_channel    VARCHAR NOT NULL,
                policy            VARCHAR NOT NULL
                                     CHECK (policy IN ('none', 'completion_blocked', 'all')),
                chat_visible      BOOLEAN DEFAULT TRUE,
                prompt_visibility BOOLEAN DEFAULT FALSE,
                created_at        TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO notifications__new (
                id, agent_id, task_id, activity_id, kind, content,
                source_channel, policy, chat_visible, prompt_visibility, created_at
            )
            SELECT
                id, agent_id, task_id, activity_id, kind, content,
                source_channel, policy, chat_visible, prompt_visibility, created_at
            FROM notifications
            """
        )
        con.execute("DROP TABLE notifications")
        con.execute("ALTER TABLE notifications__new RENAME TO notifications")
        logger.info("Migration: rebuilt notifications to add queue_visibility")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_notification_link_target_kinds(con: SQLiteCompatConnection) -> None:
    """Rebuild notification_links if it is missing host_path_consent."""
    sql = _table_sql(con, "notification_links")
    if "'host_path_consent'" in sql:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_links__new (
                notification_id VARCHAR PRIMARY KEY REFERENCES notifications(id),
                target_kind     VARCHAR NOT NULL
                                   CHECK (target_kind IN ('desk', 'host_path_consent')),
                target_path     VARCHAR NOT NULL,
                label           VARCHAR NOT NULL DEFAULT 'Open in Desk',
                created_at      TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO notification_links__new (
                notification_id, target_kind, target_path, label, created_at
            )
            SELECT notification_id, target_kind, target_path, label, created_at
            FROM notification_links
            """
        )
        con.execute("DROP TABLE notification_links")
        con.execute("ALTER TABLE notification_links__new RENAME TO notification_links")
        logger.info("Migration: rebuilt notification_links to add host_path_consent")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


# Whitespace-tolerant: the stored CREATE TABLE text keeps schema.sql's column
# alignment, which a plain substring test would be coupled to.
_NOT_NULL_MEETING_HOST_RE = re.compile(r"\bhost_agent_id\s+VARCHAR\s+NOT\s+NULL\b", re.IGNORECASE)


def _ensure_meeting_host_nullable(con: SQLiteCompatConnection) -> None:
    """Rebuild meeting_session_meta if host_agent_id is still NOT NULL.

    Deleting an agent detaches the meetings it hosted (host set to NULL) so
    the shared meeting history stays; with foreign keys on, a NOT NULL host
    made that delete fail and roll back.
    """
    sql = _table_sql(con, "meeting_session_meta")
    if not _NOT_NULL_MEETING_HOST_RE.search(sql):
        return
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS meeting_session_meta__new (
                session_id        VARCHAR PRIMARY KEY REFERENCES meeting_sessions(id),
                host_agent_id     VARCHAR REFERENCES agents(id),
                meeting_mode      VARCHAR NOT NULL
                                      CHECK (meeting_mode IN ('room', 'remote')),
                phase             VARCHAR NOT NULL
                                      CHECK (phase IN ('assembling', 'active', 'ended', 'canceled')),
                context_packet_id VARCHAR REFERENCES meeting_context_packets(id),
                kickoff_round_id  VARCHAR REFERENCES meeting_response_rounds(id),
                created_at        TIMESTAMP DEFAULT current_timestamp,
                updated_at        TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO meeting_session_meta__new (
                session_id, host_agent_id, meeting_mode, phase,
                context_packet_id, kickoff_round_id, created_at, updated_at
            )
            SELECT
                session_id, host_agent_id, meeting_mode, phase,
                context_packet_id, kickoff_round_id, created_at, updated_at
            FROM meeting_session_meta
            """
        )
        con.execute("DROP TABLE meeting_session_meta")
        con.execute("ALTER TABLE meeting_session_meta__new RENAME TO meeting_session_meta")
        logger.info("Migration: rebuilt meeting_session_meta to make host_agent_id nullable")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_cli_approval_origin_schema(con: SQLiteCompatConnection) -> None:
    """Allow CLI approval cards to stamp a thread origin and persist inline."""
    notifications_sql = _table_sql(con, "notifications")
    if "'cli_approval'" not in notifications_sql:
        con.execute("PRAGMA foreign_keys = OFF")
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications__new (
                    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                    agent_id          VARCHAR NOT NULL REFERENCES agents(id),
                    task_id           VARCHAR REFERENCES tasks(id),
                    activity_id       VARCHAR REFERENCES activities(id),
                    kind              VARCHAR NOT NULL
                                         CHECK (kind IN (
                                             'receipt', 'completion', 'blocked', 'handoff',
                                             'abandoned', 'task_update', 'host_path_consent',
                                             'cli_approval', 'queue_visibility'
                                         )),
                    content           TEXT NOT NULL,
                    source_channel    VARCHAR NOT NULL,
                    policy            VARCHAR NOT NULL
                                         CHECK (policy IN ('none', 'completion_blocked', 'all')),
                    chat_visible      BOOLEAN DEFAULT TRUE,
                    prompt_visibility BOOLEAN DEFAULT FALSE,
                    created_at        TIMESTAMP DEFAULT current_timestamp
                )
                """
            )
            con.execute(
                """
                INSERT INTO notifications__new (
                    id, agent_id, task_id, activity_id, kind, content,
                    source_channel, policy, chat_visible, prompt_visibility, created_at
                )
                SELECT
                    id, agent_id, task_id, activity_id, kind, content,
                    source_channel, policy, chat_visible, prompt_visibility, created_at
                FROM notifications
                """
            )
            con.execute("DROP TABLE notifications")
            con.execute("ALTER TABLE notifications__new RENAME TO notifications")
            logger.info("Migration: rebuilt notifications to add cli_approval")
        finally:
            con.execute("PRAGMA foreign_keys = ON")

    links_sql = _table_sql(con, "notification_links")
    if "'cli_approval'" not in links_sql:
        con.execute("PRAGMA foreign_keys = OFF")
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_links__new (
                    notification_id VARCHAR PRIMARY KEY REFERENCES notifications(id),
                    target_kind     VARCHAR NOT NULL
                                       CHECK (target_kind IN (
                                           'desk', 'host_path_consent', 'cli_approval'
                                       )),
                    target_path     VARCHAR NOT NULL,
                    label           VARCHAR NOT NULL DEFAULT 'Open in Desk',
                    created_at      TIMESTAMP DEFAULT current_timestamp
                )
                """
            )
            con.execute(
                """
                INSERT INTO notification_links__new (
                    notification_id, target_kind, target_path, label, created_at
                )
                SELECT notification_id, target_kind, target_path, label, created_at
                FROM notification_links
                """
            )
            con.execute("DROP TABLE notification_links")
            con.execute("ALTER TABLE notification_links__new RENAME TO notification_links")
            logger.info("Migration: rebuilt notification_links to add cli_approval")
        finally:
            con.execute("PRAGMA foreign_keys = ON")


_AGENT_TEMPLATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_templates_pack "
    "ON agent_templates(pack_id) WHERE pack_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_templates_url "
    "ON agent_templates(source_url) WHERE pack_id IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_templates_local "
    "ON agent_templates(title) WHERE source = 'local'",
)


def _ensure_agent_template_local_source(con: SQLiteCompatConnection) -> None:
    """Rebuild agent_templates so it can hold the operator's own templates.

    A local template (``source = 'local'``) is saved from an agent form, so it
    was never fetched and has no ``commit_sha`` or ``content_hash`` — both
    were ``NOT NULL`` — and the ``source`` CHECK allowed only ``'catalog'``
    and ``'url'``. SQLite cannot alter a CHECK or drop a NOT NULL in place, so
    the table is rebuilt: a new table with the current DDL, every row copied
    column by column, the old table dropped, the new one renamed.

    Idempotent: a table whose DDL already names ``'local'`` (every fresh
    database, and any database this has already run on) is left alone.
    Preserves every row, and never deletes the database.

    The three indexes are recreated here because ``init_db`` applies
    schema.sql BEFORE the migrations: the indexes schema.sql created were on
    the old table and were dropped with it.

    Two things beyond the file's other rebuilds. It runs inside one
    transaction, so a failure between the drop and the rename rolls back to
    the old table instead of leaving the library without one. And the new
    table is created WITHOUT ``IF NOT EXISTS``: a stray
    ``agent_templates__new`` is a state this migration cannot have produced,
    and copying rows into a table of unknown shape would be a silent guess.

    Raises ``sqlite3.Error`` from whichever statement failed, after rolling
    the transaction back.
    """
    sql = _table_sql(con, "agent_templates")
    if "'local'" in sql:
        return
    # A no-op inside a transaction, so it is switched off before BEGIN.
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                """
                CREATE TABLE agent_templates__new (
                    id                   VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                    source               VARCHAR NOT NULL CHECK (source IN ('catalog', 'url', 'local')),
                    pack_id              VARCHAR,
                    source_url           TEXT,
                    category             VARCHAR NOT NULL,
                    title                VARCHAR NOT NULL,
                    specialty            TEXT NOT NULL,
                    description          TEXT NOT NULL,
                    what_done_looks_like TEXT NOT NULL,
                    personality_hint     VARCHAR,
                    tools_hint           TEXT NOT NULL DEFAULT '[]',
                    communication        TEXT,
                    author_name          VARCHAR,
                    author_url           TEXT,
                    commit_sha           VARCHAR,
                    content_hash         VARCHAR,
                    installed_at         TIMESTAMP DEFAULT current_timestamp,
                    updated_at           TIMESTAMP DEFAULT current_timestamp,
                    CHECK ((source = 'local') = (commit_sha IS NULL)),
                    CHECK ((source = 'local') = (content_hash IS NULL)),
                    CHECK (source <> 'local' OR (pack_id IS NULL AND source_url IS NULL))
                )
                """
            )
            con.execute(
                """
                INSERT INTO agent_templates__new (
                    id, source, pack_id, source_url, category, title, specialty,
                    description, what_done_looks_like, personality_hint, tools_hint,
                    communication, author_name, author_url, commit_sha, content_hash,
                    installed_at, updated_at
                )
                SELECT
                    id, source, pack_id, source_url, category, title, specialty,
                    description, what_done_looks_like, personality_hint, tools_hint,
                    communication, author_name, author_url, commit_sha, content_hash,
                    installed_at, updated_at
                FROM agent_templates
                """
            )
            con.execute("DROP TABLE agent_templates")
            con.execute("ALTER TABLE agent_templates__new RENAME TO agent_templates")
            for index_sql in _AGENT_TEMPLATE_INDEXES:
                con.execute(index_sql)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        logger.info("Migration: rebuilt agent_templates to allow local templates")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _create_task_events_table_if_missing(con: SQLiteCompatConnection) -> None:
    """Backfill the durable task-events table for existing databases."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS task_events (
            id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
            task_id           VARCHAR NOT NULL REFERENCES tasks(id),
            author_type       VARCHAR NOT NULL
                                  CHECK (author_type IN ('human', 'agent', 'system')),
            author_agent_id   VARCHAR REFERENCES agents(id),
            author_name       VARCHAR NOT NULL,
            event_type        VARCHAR NOT NULL
                                  CHECK (event_type IN (
                                      'comment',
                                      'clarification',
                                      'answer',
                                      'status_update',
                                      'blocker',
                                      'completion',
                                      'assignment',
                                      'reprioritized',
                                      'system'
                                  )),
            content           TEXT NOT NULL,
            source_message_id VARCHAR,
            source_trigger_id VARCHAR,
            created_at        TIMESTAMP DEFAULT current_timestamp
        )
        """
    )


def _table_sql(con: SQLiteCompatConnection, table: str) -> str:
    """Return the normalized CREATE TABLE SQL for one table."""
    row = con.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        [table],
    ).fetchone()
    return str(row[0] or "") if row else ""


def _ensure_agent_state_status_values(con: SQLiteCompatConnection) -> None:
    """Rebuild agent_state if it is missing the newer visible statuses."""
    sql = _table_sql(con, "agent_state")
    if "'waiting'" in sql and "'blocked'" in sql:
        return

    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_state__new (
                agent_id        VARCHAR PRIMARY KEY REFERENCES agents(id),
                x               INTEGER DEFAULT 0,
                y               INTEGER DEFAULT 0,
                status          VARCHAR DEFAULT 'idle'
                                    CHECK (status IN ('idle', 'waiting', 'blocked', 'work_active', 'social_active', 'in_transit')),
                last_active_at  TIMESTAMP,
                idle_since      TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO agent_state__new (agent_id, x, y, status, last_active_at, idle_since)
            SELECT agent_id, x, y, status, last_active_at, idle_since
            FROM agent_state
            """
        )
        con.execute("DROP TABLE agent_state")
        con.execute("ALTER TABLE agent_state__new RENAME TO agent_state")
        logger.info("Migration: rebuilt agent_state to add waiting/blocked statuses")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def _ensure_task_status_values(con: SQLiteCompatConnection) -> None:
    """Rebuild tasks if it is missing waiting or cancelled."""
    sql = _table_sql(con, "tasks")
    if "'waiting'" in sql and "'cancelled'" in sql:
        return

    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks__new (
                id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
                title          VARCHAR NOT NULL,
                description    TEXT,
                project        VARCHAR,
                assigned_to    VARCHAR,
                requester_id   VARCHAR,
                owner_id       VARCHAR,
                created_by     VARCHAR,
                status         VARCHAR DEFAULT 'pending'
                                   CHECK (status IN ('pending', 'accepted', 'active', 'waiting', 'blocked', 'complete',
                                                     'stalled', 'abandoned', 'delegated', 'declined', 'cancelled')),
                parent_task_id VARCHAR,
                cost_ceiling   DECIMAL,
                completion_summary TEXT,
                status_note    TEXT,
                watchdog_pinged_at TIMESTAMP,
                last_progress_at TIMESTAMP DEFAULT current_timestamp,
                last_heartbeat_at TIMESTAMP DEFAULT current_timestamp,
                last_activity  TIMESTAMP DEFAULT current_timestamp,
                created_at     TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        con.execute(
            """
            INSERT INTO tasks__new (
                id, title, description, project, assigned_to, requester_id, owner_id, created_by,
                status, parent_task_id, cost_ceiling, completion_summary, status_note,
                watchdog_pinged_at, last_progress_at, last_heartbeat_at, last_activity, created_at
            )
            SELECT
                id, title, description, project, assigned_to, requester_id, owner_id, created_by,
                status, parent_task_id, cost_ceiling, completion_summary, status_note,
                watchdog_pinged_at, last_progress_at, last_heartbeat_at, last_activity, created_at
            FROM tasks
            """
        )
        con.execute("DROP TABLE tasks")
        con.execute("ALTER TABLE tasks__new RENAME TO tasks")
        logger.info("Migration: rebuilt tasks to add waiting/cancelled statuses")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def close_thread_connection() -> None:
    """Close the current thread's SQLite connection."""
    con = getattr(_thread_local, "connection", None)
    if con is None:
        return
    with _connection_lock:
        _thread_connections.pop(threading.get_ident(), None)
    _close_safely(con)
    _thread_local.connection = None


def interrupt_thread_connection(thread_id: int | None) -> None:
    """Interrupt the active SQLite query for a specific thread, if present."""
    if thread_id is None:
        return
    with _connection_lock:
        con = _thread_connections.get(thread_id)
    if con is None:
        return
    try:
        con.interrupt()
    except Exception:
        pass


def close_connection() -> None:
    """Close all SQLite connections for this process."""
    with _connection_lock:
        _invalidate_all_thread_connections_unlocked()
    logger.info("SQLite connections closed")


def init_db() -> None:
    """Initialise the database and seed default settings."""
    con = get_connection()
    _apply_schema(con)
    _apply_migrations(con)

    from db.settings import prune_obsolete_settings, seed_defaults
    seed_defaults()
    prune_obsolete_settings()

    from db.ai_personalities import seed_default_personalities
    seed_default_personalities()

    from db.cli_policy_rules import reconcile_hardened_seed_rules, seed_default_rules
    seed_default_rules()
    # Insert hardened rows that are missing. Do not overwrite operator tiers.
    reconcile_hardened_seed_rules()

    from db.agent_storage_identities import ensure_all_agent_storage_identities
    ensure_all_agent_storage_identities()

    # The orphan cleanup (AgentRepository.purge_orphans) is NOT run here: this
    # function also runs on every runtime start (services.start, the worker),
    # and the cleanup runs once per app start, from main.py's lifespan.

    from db.agent_storage import normalize_agent_personal_storage_roots
    normalize_agent_personal_storage_roots()

    # After the storage-key normalization above, so a legacy agent-written
    # /me/standing_prefs.json sits under agents/<storage_key>. Idempotent.
    from core.agent_loop.standing_prefs import migrate_workspace_standing_prefs
    migrate_workspace_standing_prefs()

    # After the floors migration: every floor gets its company folder. This is
    # also where a retired BOSSMOD_PROJECTS_ROOT stops startup.
    from core.bm_cli.floor_roots import ensure_floor_roots
    ensure_floor_roots()

    # Needs Lobby's folder (above). Runs once; see db/company_layout.py.
    from db.company_layout import migrate_to_floor_layout
    migrate_to_floor_layout()

    from db.secret_store import migrate_plaintext_secrets
    migrated = migrate_plaintext_secrets()
    if migrated:
        logger.info("Encrypted %d leftover plaintext secret column(s)", migrated)

    logger.info("Database initialised")


def backup_database(dest_dir: Path) -> Path:
    """Write a consistent copy of the configured SQLite database into *dest_dir*.

    Uses sqlite3's online backup API from a dedicated read connection, not the
    thread-local one: ``reset_database`` closes every connection before it
    backs up, and the backup API copies a consistent snapshot that already
    includes committed WAL content, so no ``-wal``/``-shm`` sidecar is needed.
    The copy is written under a ``.partial`` name and renamed into place, so a
    failed backup never leaves a file that looks complete.

    Args:
        dest_dir: Directory to write into. Created when missing.

    Returns:
        The backup's path, ``<db file name>.<UTC timestamp, µs>.bak``.

    Raises:
        FileNotFoundError: The database file does not exist.
        FileExistsError: A backup with this timestamp is already there.
        OSError, sqlite3.Error: The copy failed. Nothing is left at the
            returned name.
    """
    db_path = Path(_DB_PATH)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Microseconds: two backups in one second (a retried boot) must not collide.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = dest_dir / f"{db_path.name}.{stamp}.bak"
    if target.exists():
        raise FileExistsError(f"Database backup already exists: {target}")
    partial = target.with_name(f"{target.name}.partial")
    source = sqlite3.connect(db_path, timeout=30.0)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
        finally:
            copy.close()
        os.replace(partial, target)
    except BaseException:
        # Our own half-written copy, never operator data.
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return target


def reset_database() -> None:
    """Recreate the database file from the current schema and seed data.

    Clears per-agent artifact workspaces and the system-owned standing prefs
    so reseeded agents start clean.

    Note: Shared project files under the company root are preserved. A reset
    does not move or delete them.
    """
    import shutil

    from core.bm_cli.filesystem import (
        agents_artifact_root,
        artifacts_root,
        ensure_artifact_roots,
        standing_prefs_root,
    )

    close_connection()
    db_path = Path(_DB_PATH)
    if db_path.exists():
        # Safety rail: always create a timestamped backup before deleting.
        # The artifacts root, so a test run's reset stays in its temp tree.
        backup_dir = artifacts_root() / "db_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            backup_base = backup_database(backup_dir)
            logger.warning("Database reset requested; backup created at %s", str(backup_base))
        except Exception:
            logger.exception("Failed to create DB backup before reset; proceeding with reset anyway")
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    # Wipe per-agent workspaces so filesystem matches fresh DB agents.
    root = agents_artifact_root()
    if root.exists():
        shutil.rmtree(root)
    ensure_artifact_roots()
    # Standing prefs are keyed by storage key; reseeded agents reuse those keys.
    shutil.rmtree(standing_prefs_root())

    init_db()
