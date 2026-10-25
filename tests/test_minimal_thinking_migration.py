"""Existing CHECK widening preserves data, schema objects and foreign keys."""
import sqlite3

from db.connection import SQLiteCompatConnection, _ensure_minimal_thinking_choice


def test_minimal_upgrade_is_atomic_and_idempotent():
    raw = sqlite3.connect(':memory:', isolation_level=None)
    con = SQLiteCompatConnection(raw)
    con.execute('PRAGMA foreign_keys = ON')
    for table in ('agents', 'agent_snapshots'):
        con.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, thinking_social TEXT CHECK (thinking_social IN ('default','off','low','medium','high','xhigh')), thinking_work TEXT CHECK (thinking_work IN ('default','off','low','medium','high','xhigh')), extra TEXT)")
        con.execute(f"INSERT INTO {table} VALUES ('a', 'off', 'high', 'preserved')")
        con.execute(f'CREATE INDEX idx_{table}_extra ON {table}(extra)')
    con.execute('CREATE TABLE refs (agent_id TEXT REFERENCES agents(id))')
    con.execute("INSERT INTO refs VALUES ('a')")
    con.execute('CREATE TABLE audit (id TEXT)')
    con.execute('CREATE TRIGGER track_agent AFTER UPDATE ON agents BEGIN INSERT INTO audit VALUES (new.id); END')
    _ensure_minimal_thinking_choice(con)
    _ensure_minimal_thinking_choice(con)
    assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert con.execute('PRAGMA foreign_key_check').fetchall() == []
    for table in ('agents', 'agent_snapshots'):
        assert con.execute(f'SELECT * FROM {table}').fetchone() == ('a', 'off', 'high', 'preserved')
        con.execute(f"UPDATE {table} SET thinking_social = 'minimal'")
        assert con.execute("SELECT name FROM sqlite_master WHERE name = ?", [f'idx_{table}_extra']).fetchone()
    assert con.execute('SELECT * FROM audit').fetchall() == [('a',)]
    raw.close()
