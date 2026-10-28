"""Thinking-choice CHECK widening follows the vocabulary and preserves everything else."""
import os
import sqlite3
from pathlib import Path

import pytest

from core.models.thinking import THINKING_CHOICES
from db.connection import SQLiteCompatConnection, _ensure_thinking_choices, _table_sql

_TABLES = ('agents', 'agent_snapshots')
_PRE_MINIMAL = ('default', 'off', 'low', 'medium', 'high', 'xhigh')
_PRE_MAX = ('default', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh')


def _check(column, values):
    return f"CHECK ({column} IN ({','.join(repr(v) for v in values)}))"


def _database(lists):
    """An in-memory DB whose two tables carry the given CHECK lists, a row, an index, a trigger and an FK."""
    raw = sqlite3.connect(':memory:', isolation_level=None)
    con = SQLiteCompatConnection(raw)
    con.execute('PRAGMA foreign_keys = ON')
    for table in _TABLES:
        values = lists[table]
        con.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, "
                    f"thinking_social TEXT NOT NULL DEFAULT 'default' {_check('thinking_social', values)}, "
                    f"thinking_work TEXT NOT NULL DEFAULT 'default' {_check('thinking_work', values)}, extra TEXT)")
        con.execute(f"INSERT INTO {table} VALUES ('a', 'off', 'high', 'preserved')")
        con.execute(f'CREATE INDEX idx_{table}_extra ON {table}(extra)')
    con.execute('CREATE TABLE refs (agent_id TEXT REFERENCES agents(id))')
    con.execute("INSERT INTO refs VALUES ('a')")
    con.execute('CREATE TABLE audit (id TEXT)')
    con.execute('CREATE TRIGGER track_agent AFTER UPDATE ON agents BEGIN INSERT INTO audit VALUES (new.id); END')
    return raw, con


def _schema_version(con):
    return con.execute('PRAGMA schema_version').fetchone()[0]


@pytest.mark.parametrize('stored', [_PRE_MINIMAL, _PRE_MAX], ids=['pre-minimal', 'pre-max'])
def test_an_older_list_widens_atomically_and_idempotently(stored):
    raw, con = _database({table: stored for table in _TABLES})
    _ensure_thinking_choices(con)
    widened = _schema_version(con)
    _ensure_thinking_choices(con)
    assert _schema_version(con) == widened
    assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert con.execute('PRAGMA foreign_key_check').fetchall() == []
    for table in _TABLES:
        sql = _table_sql(con, table)
        for column in ('thinking_social', 'thinking_work'):
            assert _check(column, THINKING_CHOICES) in sql
        assert con.execute(f'SELECT * FROM {table}').fetchone() == ('a', 'off', 'high', 'preserved')
        for choice in THINKING_CHOICES:
            con.execute(f'UPDATE {table} SET thinking_social = ?, thinking_work = ?', [choice, choice])
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(f"UPDATE {table} SET thinking_work = 'ultra'")
        assert con.execute("SELECT name FROM sqlite_master WHERE name = ?", [f'idx_{table}_extra']).fetchone()
    assert con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall() == [('track_agent',)]
    assert con.execute('SELECT * FROM audit').fetchall() == [('a',)] * len(THINKING_CHOICES)
    raw.close()


def test_a_current_list_is_left_alone():
    raw, con = _database({table: THINKING_CHOICES for table in _TABLES})
    before = _schema_version(con)
    _ensure_thinking_choices(con)
    assert _schema_version(con) == before
    raw.close()


def test_one_old_table_is_rebuilt_and_the_current_one_is_not():
    raw, con = _database({'agents': THINKING_CHOICES, 'agent_snapshots': _PRE_MAX})
    agents = _table_sql(con, 'agents')
    _ensure_thinking_choices(con)
    assert _table_sql(con, 'agents') == agents
    assert _check('thinking_work', THINKING_CHOICES) in _table_sql(con, 'agent_snapshots')
    raw.close()


def test_a_stored_value_outside_the_vocabulary_refuses_to_narrow_before_any_change():
    raw, con = _database({'agents': _PRE_MAX, 'agent_snapshots': (*THINKING_CHOICES, 'ultra')})
    before = _schema_version(con)
    with pytest.raises(RuntimeError, match='agent_snapshots.thinking_social allows ultra.*refusing to narrow'):
        _ensure_thinking_choices(con)
    assert _schema_version(con) == before
    assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    raw.close()


def test_a_missing_check_is_an_error_not_a_skip():
    raw = sqlite3.connect(':memory:', isolation_level=None)
    con = SQLiteCompatConnection(raw)
    con.execute(f"CREATE TABLE agents (id TEXT, thinking_social TEXT {_check('thinking_social', _PRE_MAX)}, thinking_work TEXT)")
    con.execute(f"CREATE TABLE agent_snapshots (id TEXT, thinking_social TEXT {_check('thinking_social', _PRE_MAX)}, "
                f"thinking_work TEXT {_check('thinking_work', _PRE_MAX)})")
    with pytest.raises(RuntimeError, match=r'agents\.thinking_work: expected one CHECK'):
        _ensure_thinking_choices(con)
    raw.close()


def test_a_fresh_database_already_matches_the_vocabulary():
    import db
    from db.connection import get_connection
    db.close_connection()
    path = Path(os.environ['BOSSMOD_DB_PATH'])
    for suffix in ('', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db.init_db()
    con = get_connection()
    for table in _TABLES:
        sql = _table_sql(con, table)
        for column in ('thinking_social', 'thinking_work'):
            assert _check(column, THINKING_CHOICES) in sql, (table, column)
    before = _schema_version(con)
    _ensure_thinking_choices(con)
    assert _schema_version(con) == before
    db.close_connection()
