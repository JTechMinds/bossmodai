"""File reads (`cat`, `rr`) share one character budget and page instead of failing.

Covers the pure windowing in ``core.bm_cli.read_window``, both handlers end to
end through ``execute_bm_cli``, and the ``cli_max_read_chars`` setting's write
boundary and the retirement of the old ``cli_max_read_lines`` line cap.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.read_window import LineCut, ReadWindow, read_on_note, take_read_window
from core.bm_cli.runtime import execute_bm_cli
from db.crud import execute, query_one
from db.settings import prune_obsolete_settings

_READ_ON_RE = re.compile(r"Read on with: (rr .+)$", re.MULTILINE)


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


def _agent():
    agent = db.create_agent("Page Reader", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state, agent_artifact_dir(agent.storage_key)


def _set_budget(chars: int) -> None:
    db.set_setting("cli_max_read_chars", str(chars), "advanced")
    config.reload()


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


# ── windowing, pure ───────────────────────────────────────────────────


def test_whole_lines_are_taken_while_they_fit_the_budget() -> None:
    window = take_read_window(["aaaa", "bbbb", "cccc"], start=1, end=3, max_chars=12, numbered=False)

    # Each line costs 4 + 1; a third would bring the total to 15.
    assert window == ReadWindow(first=1, last=2, requested_last=3, rendered=("aaaa", "bbbb"), cut=None)
    assert window.next_start == 3
    assert window.complete is False


def test_the_rendered_cost_counts_the_gutter_and_the_newline() -> None:
    lines = ["x", "y"]
    # "   1 | x" is 8 characters, plus 1 for its newline.
    one = take_read_window(lines, start=1, end=2, max_chars=17, numbered=True)
    both = take_read_window(lines, start=1, end=2, max_chars=18, numbered=True)

    assert one.rendered == ("   1 | x",)
    assert one.next_start == 2
    assert both.rendered == ("   1 | x", "   2 | y")
    assert both.complete is True


def test_an_over_long_first_line_is_cut_and_recorded() -> None:
    lines = ["y" * 50, "z"]

    numbered = take_read_window(lines, start=1, end=2, max_chars=20, numbered=True)
    assert numbered.rendered == ("   1 | " + "y" * 13,)
    assert numbered.cut == LineCut(line=1, total_chars=50, shown_chars=13)
    assert (numbered.last, numbered.next_start, numbered.complete) == (1, 2, False)

    bare = take_read_window(lines, start=1, end=1, max_chars=20, numbered=False)
    assert bare.rendered == ("y" * 20,)
    assert bare.cut == LineCut(line=1, total_chars=50, shown_chars=20)
    assert bare.next_start is None
    assert bare.complete is False

    below_gutter = take_read_window(lines, start=1, end=1, max_chars=3, numbered=True)
    assert below_gutter.rendered == ("   ",)
    assert below_gutter.cut == LineCut(line=1, total_chars=50, shown_chars=0)


def test_only_the_first_line_is_ever_cut() -> None:
    window = take_read_window(["ab", "c" * 50], start=1, end=2, max_chars=20, numbered=False)

    assert window.rendered == ("ab",)
    assert window.cut is None
    assert window.next_start == 2


def test_next_start_and_complete_at_the_boundaries() -> None:
    lines = ["abc", "def"]

    exactly = take_read_window(lines, start=1, end=2, max_chars=8, numbered=False)
    assert exactly.last == 2
    assert exactly.next_start is None
    assert exactly.complete is True

    one_over = take_read_window(lines, start=1, end=2, max_chars=7, numbered=False)
    assert one_over.last == 1
    assert one_over.next_start == 2
    assert one_over.complete is False

    last_line = take_read_window(lines, start=2, end=2, max_chars=1000, numbered=True)
    assert (last_line.first, last_line.last, last_line.requested_last) == (2, 2, 2)
    assert last_line.complete is True

    # A first line exactly the budget long is shown whole, not cut.
    full_width = take_read_window(["x" * 10, "y"], start=1, end=2, max_chars=10, numbered=False)
    assert full_width.rendered == ("x" * 10,)
    assert full_width.cut is None
    assert full_width.next_start == 2


@pytest.mark.parametrize(
    ("lines", "start", "end", "max_chars"),
    [
        (["a", "b"], 0, 1, 10),
        (["a", "b"], 2, 1, 10),
        (["a", "b"], 1, 3, 10),
        ([], 1, 1, 10),
        (["a", "b"], 1, 2, 0),
    ],
)
def test_a_bad_window_raises(lines: list[str], start: int, end: int, max_chars: int) -> None:
    with pytest.raises(ValueError):
        take_read_window(lines, start=start, end=end, max_chars=max_chars, numbered=True)


def test_read_on_note_wording_and_a_quoted_path() -> None:
    complete = take_read_window(["a"], start=1, end=1, max_chars=100, numbered=True)
    assert read_on_note(complete, path="/me/a.md", max_chars=100) is None

    partial = take_read_window(["aaaa"] * 5, start=1, end=5, max_chars=10, numbered=False)
    note = read_on_note(partial, path="/me/my notes.md", max_chars=10)
    assert note == (
        "Stopped after line 2 of 5: one read returns up to 10 characters. "
        "Read on with: rr '/me/my notes.md' 3:5"
    )
    command = _READ_ON_RE.search(note).group(1)
    assert shlex.split(command) == ["rr", "/me/my notes.md", "3:5"]

    cut = take_read_window(["y" * 40_000, "z"], start=1, end=2, max_chars=30_000, numbered=True)
    assert read_on_note(cut, path="/me/min.js", max_chars=30_000) == (
        "Line 1 has 40,000 characters; only the first 29,993 are shown.\n"
        "Stopped after line 1 of 2: one read returns up to 30,000 characters. "
        "Read on with: rr /me/min.js 2:2"
    )


# ── rr ────────────────────────────────────────────────────────────────


def test_rr_returns_a_260_line_range_whole_at_the_default_budget() -> None:
    agent, state, me = _agent()
    lines = [f"{n:03d} " + "x" * 48 for n in range(1, 301)]
    (me / "module.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = execute_bm_cli(agent, state, "rr /me/module.py 1:260")

    assert result.ok is True, result.prompt_content
    assert result.data["end"] == 260
    assert [item["line"] for item in result.data["lines"]] == list(range(1, 261))
    assert "range: 1:260" in result.prompt_content
    assert " 260 | 260 " in result.prompt_content
    assert "Read on with" not in result.prompt_content
    assert "Stopped after" not in result.prompt_content


def test_rr_over_the_budget_returns_a_prefix_and_reads_on_from_the_note() -> None:
    agent, state, me = _agent()
    lines = [f"line {n:02d}" for n in range(1, 21)]
    (me / "long.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # "   1 | line 01" is 14 characters plus a newline: six lines fit in 100.
    _set_budget(100)

    first = execute_bm_cli(agent, state, "rr /me/long.txt 1:20")

    assert first.ok is True, first.prompt_content
    assert first.data["start"] == 1
    assert first.data["end"] == 6
    assert first.data["lines"] == [{"line": n, "text": f"line {n:02d}"} for n in range(1, 7)]
    assert "range: 1:6" in first.prompt_content
    assert "   7 | line 07" not in first.prompt_content
    assert first.detail.endswith("read lines 1:6 from /me/long.txt")
    note = "Stopped after line 6 of 20: one read returns up to 100 characters. Read on with: rr /me/long.txt 7:20"
    assert note in first.prompt_content
    assert first.prompt_content.index(note) > first.prompt_content.index("   6 | line 06")

    seen = [item["line"] for item in first.data["lines"]]
    prompt = first.prompt_content
    while (match := _READ_ON_RE.search(prompt)) is not None:
        follow = execute_bm_cli(agent, state, match.group(1))
        assert follow.ok is True, follow.prompt_content
        assert follow.data["start"] == seen[-1] + 1
        seen.extend(item["line"] for item in follow.data["lines"])
        prompt = follow.prompt_content
    assert seen == list(range(1, 21))


def test_rr_clamps_an_end_past_the_last_line() -> None:
    agent, state, me = _agent()
    (me / "short.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

    result = execute_bm_cli(agent, state, "rr /me/short.txt 3:99")

    assert result.ok is True, result.prompt_content
    assert result.data["end"] == 5
    assert result.data["lines"] == [
        {"line": 3, "text": "three"},
        {"line": 4, "text": "four"},
        {"line": 5, "text": "five"},
    ]
    assert "range: 3:5" in result.prompt_content
    assert "Read on with" not in result.prompt_content


def test_rr_empty_file_and_out_of_range_errors_are_unchanged() -> None:
    agent, state, me = _agent()
    (me / "empty.txt").write_text("", encoding="utf-8")
    (me / "short.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

    empty = execute_bm_cli(agent, state, "rr /me/empty.txt 1:5")
    assert empty.ok is True
    assert "(empty file)" in empty.prompt_content
    assert "range: 1:0" in empty.prompt_content
    assert empty.data["lines"] == []

    past_empty = execute_bm_cli(agent, state, "rr /me/empty.txt 2:3")
    assert past_empty.ok is False
    assert past_empty.data["error"] == "Line 2 is outside the file (file is empty)."

    past_end = execute_bm_cli(agent, state, "rr /me/short.txt 10:12")
    assert past_end.ok is False
    assert past_end.data["error"] == "Line 10 is outside the file (last line is 5)."


# ── cat ───────────────────────────────────────────────────────────────


def test_cat_shows_a_small_file_verbatim() -> None:
    agent, state, me = _agent()
    body = "alpha\n  beta  \n\ntrailing text without newline"
    (me / "notes.md").write_text(body, encoding="utf-8")

    result = execute_bm_cli(agent, state, "cat /me/notes.md")

    assert result.ok is True, result.prompt_content
    assert result.data["content"] == body
    assert result.detail.endswith("read /me/notes.md")
    assert "Read on with" not in result.prompt_content


def test_cat_over_the_budget_shows_leading_lines_and_the_read_on_note() -> None:
    agent, state, me = _agent()
    lines = [f"row {n:02d}" for n in range(1, 31)]
    (me / "big.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # "row 01" is 6 characters plus a newline: fourteen lines fit in 100.
    _set_budget(100)

    result = execute_bm_cli(agent, state, "cat /me/big.txt")

    shown = "\n".join(lines[:14])
    assert result.ok is True, result.prompt_content
    assert result.data["content"] == shown
    assert shown in result.prompt_content
    assert "row 15" not in result.prompt_content
    assert result.detail.endswith("read lines 1:14 of /me/big.txt")
    assert (
        "Stopped after line 14 of 30: one read returns up to 100 characters. "
        "Read on with: rr /me/big.txt 15:30"
    ) in result.prompt_content
    assert "Read on with" not in result.data["content"]
    assert "..." not in result.prompt_content


# ── settings ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["0", "abc"])
def test_the_read_limit_api_refuses_anything_but_a_whole_number_of_at_least_one(bad: str) -> None:
    client, headers = _client()

    refused = client.put(
        "/api/settings/cli_max_read_chars", params={"value": bad, "category": "advanced"}, headers=headers,
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == "CLI Read Limit must be a whole number of at least 1."
    assert query_one("SELECT value FROM settings WHERE key = 'cli_max_read_chars'") == {"value": "30000"}


def test_prune_deletes_a_leftover_line_cap_row() -> None:
    execute(
        "INSERT INTO settings (key, value, category, updated_at) "
        "VALUES ($1, $2, $3, current_timestamp)",
        ["cli_max_read_lines", "200", "advanced"],
    )

    prune_obsolete_settings()

    assert query_one("SELECT value FROM settings WHERE key = 'cli_max_read_lines'") is None


def test_the_line_cap_setting_cannot_be_written() -> None:
    with pytest.raises(ValueError, match="cli_max_read_lines"):
        db.set_setting("cli_max_read_lines", "500", "advanced")
