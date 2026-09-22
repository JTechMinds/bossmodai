"""Agent snapshots — what Add agent's Recent scope lists.

One current copy of each agent's setup, including agents that have since been
deleted, captured by the db layer on create, save, prompt-history policy
change and delete (db/agent_snapshots.py). Read-only here: nothing in the API
writes a snapshot directly, and recreating one is an ordinary
``POST /api/agents`` from a create form it prefilled.
"""

from __future__ import annotations

from fastapi import APIRouter

from core.models import AgentSnapshot
import db

router = APIRouter()


@router.get("/agent-snapshots")
def list_agent_snapshots() -> list[AgentSnapshot]:
    """Return every agent snapshot, newest ``captured_at`` first.

    One local read, already trimmed to ``recent_agents_limit`` by the captures
    that wrote it. A deleted agent's snapshot carries ``deleted_at``. No row
    holds an API key, base URL or extra body — the table has no column for
    them — so this is safe to hand to the page as it stands. An empty table is
    an empty list, not an error.
    """
    return db.list_agent_snapshots()
