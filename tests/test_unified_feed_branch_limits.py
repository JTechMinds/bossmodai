"""Phase 3 (refresh efficiency): the unified feed caps each source before merging.

``db/unified_feed.py`` used to sort and page the whole of ``activity_log``,
``activities`` and ``notifications`` merged together. Each source now sends
only its newest ``offset + limit + 1`` matching rows into the merge. These
tests hold the page to exactly what the whole-table query returned, for
every limit, offset and filter the ``/api/activity/feed`` route accepts.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from db.crud import execute, query
from db.unified_feed import _UNION_SQL, classify_category, get_unified_feed

# The pre-change query: merge every row of all three sources, then filter,
# sort and page. Phase 4 made the feed order total (timestamps tie), so the
# oracle carries the same tiebreak: source name, then rowid newest first.
_WHOLE_TABLE_SQL = """
WITH unified AS (
    SELECT id, 'activity_log' AS source, event, detail AS title,
           NULL AS detail_text, agent_name, NULL AS task_id,
           NULL AS metadata, created_at AS ts, FALSE AS is_active,
           rowid AS seq
    FROM activity_log

    UNION ALL

    SELECT a.id, 'activity' AS source, a.kind AS event,
           COALESCE(a.title, a.kind || ' (' || a.status || ')') AS title,
           a.detail AS detail_text, ag.name AS agent_name, a.task_id,
           a.metadata, COALESCE(a.updated_at, a.created_at) AS ts,
           (a.status IN ('active', 'paused')) AS is_active,
           a.rowid AS seq
    FROM activities a
    LEFT JOIN agents ag ON ag.id = a.agent_id

    UNION ALL

    SELECT n.id, 'notification' AS source, n.kind AS event,
           n.content AS title, NULL AS detail_text,
           ag.name AS agent_name, n.task_id,
           NULL AS metadata, n.created_at AS ts, FALSE AS is_active,
           n.rowid AS seq
    FROM notifications n
    LEFT JOIN agents ag ON ag.id = n.agent_id
)
SELECT * FROM unified
WHERE ($1 IS NULL OR LOWER(title) LIKE '%' || LOWER($1) || '%')
  AND ($2 IS NULL OR agent_name = $2)
ORDER BY is_active DESC, ts DESC, source, seq DESC
LIMIT $3 OFFSET $4
"""


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _seed(*, same_second: bool = False) -> list[str]:
    """Rows in all three sources.

    By default every timestamp is distinct. With ``same_second`` they are
    drawn from five whole seconds, so most rows tie on the timestamp and the
    page order rests on the tiebreak (source, then rowid).
    """
    rng = random.Random(20261005)
    agents = [db.create_agent(f"Feed {index}", role="Eng", desk_x=1 + index, desk_y=3) for index in range(3)]
    names = [agent.name for agent in agents]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seconds = iter(rng.sample(range(1, 1_000_000), 400))

    def at() -> datetime:
        if same_second:
            # activity_log / notifications store current_timestamp text; the
            # activities columns hold the adapter's ISO text. Ties within each.
            return base + timedelta(seconds=rng.randrange(5))
        return base + timedelta(seconds=next(seconds))

    for index in range(110):
        execute(
            "INSERT INTO activity_log (event, detail, agent_name, created_at) VALUES ($1, $2, $3, $4)",
            [
                rng.choice(["agent_moved", "task_created", "error_x", "misc", "invalid_input", "Error_caps"]),
                f"log {index} Alpha" if index % 3 else f"log {index}",
                rng.choice([*names, None]),
                at(),
            ],
        )
    for index in range(70):
        activity = db.create_runtime_activity(
            rng.choice(agents).id,
            rng.choice(["work", "meeting", "movement", "assignment", "social"]),
            status=rng.choice(["active", "paused", "completed", "cancelled"]),
            title=f"act {index} alpha" if index % 2 else None,
        )
        execute(
            "UPDATE activities SET created_at = $1, updated_at = $2 WHERE id = $3",
            [at(), at() if index % 4 else None, activity.id],
        )
    for index in range(60):
        notification = db.create_notification(
            agent_id=rng.choice(agents).id,
            kind=rng.choice(["completion", "blocked", "receipt", "handoff", "cli_approval"]),
            content=f"note {index} ALPHA" if index % 2 else f"note {index}",
            source_channel="chat",
            policy="all",
            chat_visible=True,
            prompt_visibility=False,
        )
        execute("UPDATE notifications SET created_at = $1 WHERE id = $2", [at(), notification.id])
    return names


@pytest.mark.parametrize("limit", [1, 5, 50, 200])
def test_every_page_is_the_page_the_whole_table_query_returned(limit: int) -> None:
    names = _seed()
    for offset in (0, 1, 7, 49, 150, 400, -3):
        for search in (None, "alpha", "log 1", "zzz"):
            for agent_name in (None, names[0]):
                fetch_limit = limit + 1
                expected = query(_WHOLE_TABLE_SQL, [search, agent_name, fetch_limit, offset])
                actual = query(
                    _UNION_SQL,
                    [search, agent_name, fetch_limit, offset, fetch_limit + max(offset, 0), None],
                )
                assert actual == expected, (limit, offset, search, agent_name)


def test_the_comparison_reaches_every_source_and_both_activity_states() -> None:
    _seed()
    page = query(_WHOLE_TABLE_SQL, [None, None, 400, 0])
    assert {row["source"] for row in page} == {"activity_log", "activity", "notification"}
    assert {bool(row["is_active"]) for row in page} == {True, False}


def test_the_feed_api_pages_through_every_row_once() -> None:
    """Walking the pages the Log place loads sees each row exactly once."""
    _seed()
    seen: list[str] = []
    offset = 0
    while True:
        page = get_unified_feed(limit=25, offset=offset)
        seen.extend(entry["id"] for entry in page["entries"])
        if not page["has_more"]:
            break
        offset += 25
    assert len(seen) == len(set(seen)) == 110 + 70 + 60


def _category_rows(category: str, search: str | None = None) -> list[str]:
    """Oracle: every row of the whole-table feed whose label is ``category``, in feed order."""
    rows = query(_WHOLE_TABLE_SQL, [search, None, 10_000, 0])
    return [row["id"] for row in rows if classify_category(row["source"], row["event"]) == category]


@pytest.mark.parametrize("category", ["agent", "task", "error", "system"])
def test_a_category_page_is_a_page_of_that_category(category: str) -> None:
    _seed()
    for search in (None, "alpha"):
        expected_all = _category_rows(category, search)
        assert expected_all, (category, search)
        for limit in (1, 5, 25):
            for offset in (0, 3, 25, 60, 400):
                page = get_unified_feed(limit=limit, offset=offset, search=search, category=category)
                expected = expected_all[offset:offset + limit]
                assert [entry["id"] for entry in page["entries"]] == expected, (category, search, limit, offset)
                assert page["has_more"] == (len(expected_all) > offset + limit)
                assert all(entry["category"] == category for entry in page["entries"])


@pytest.mark.parametrize("category", ["agent", "task", "error", "system"])
def test_paging_a_category_has_no_repeats_and_no_gaps(category: str) -> None:
    _seed()
    seen: list[str] = []
    offset = 0
    while True:
        page = get_unified_feed(limit=7, offset=offset, category=category)
        seen.extend(entry["id"] for entry in page["entries"])
        if not page["has_more"]:
            break
        offset += 7
    assert seen == _category_rows(category)


def test_the_four_categories_partition_the_feed() -> None:
    _seed()
    total = query(_WHOLE_TABLE_SQL, [None, None, 10_000, 0])
    by_category = [_category_rows(category) for category in ("agent", "task", "error", "system")]
    assert sorted(row_id for rows in by_category for row_id in rows) == sorted(row["id"] for row in total)


@pytest.mark.parametrize("limit", [1, 7, 50])
def test_same_second_rows_page_the_same_as_the_whole_table_query(limit: int) -> None:
    names = _seed(same_second=True)
    whole = query(_WHOLE_TABLE_SQL, [None, None, 10_000, 0])
    # The seed really does tie: far fewer distinct timestamps than rows.
    assert len({row["ts"] for row in whole}) < len(whole) // 10
    for offset in (0, 1, 7, 49, 150, 400):
        for agent_name in (None, names[0]):
            fetch_limit = limit + 1
            expected = query(_WHOLE_TABLE_SQL, [None, agent_name, fetch_limit, offset])
            actual = query(
                _UNION_SQL,
                [None, agent_name, fetch_limit, offset, fetch_limit + max(offset, 0), None],
            )
            assert actual == expected, (limit, offset, agent_name)


@pytest.mark.parametrize("category", [None, "agent", "task", "error", "system"])
def test_same_second_paging_has_no_repeats_and_no_gaps(category: str | None) -> None:
    _seed(same_second=True)
    seen: list[str] = []
    offset = 0
    while True:
        page = get_unified_feed(limit=6, offset=offset, category=category)
        seen.extend(entry["id"] for entry in page["entries"])
        if not page["has_more"]:
            break
        offset += 6
    whole = query(_WHOLE_TABLE_SQL, [None, None, 10_000, 0])
    expected = [
        row["id"] for row in whole
        if category is None or classify_category(row["source"], row["event"]) == category
    ]
    assert seen == expected
    assert len(seen) == len(set(seen))
