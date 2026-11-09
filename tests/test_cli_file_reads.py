"""File reads (`cat`, `rr`) share one token budget and page instead of failing.

Covers the pure windowing in ``core.bm_cli.read_window`` (with a one token
per character counter, so the arithmetic is exact), both handlers end to end
through ``execute_bm_cli`` with the real tokenizer, the rule that a read's
whole result fits Largest command result so the result cap never cuts it,
and the retirement of the old ``cli_max_read_chars`` and ``cli_max_read_lines``
limits.
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
from core.bm_cli.results import wrap_cli_tool_message
from core.bm_cli.runtime import execute_bm_cli
from core.context_budget import read_context_budget
from core.llm.client import count_text_tokens
from db.crud import execute, query_one
from db.settings import prune_obsolete_settings
from tests._context_budget import set_context_budget

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


def _set_result_budget(tokens: int) -> None:
    """Largest command result: 5% of a usable window twenty times ``tokens``."""
    assert set_context_budget(usable=tokens * 20, result=5).result == tokens


def _window(lines: list[str], *, start: int, end: int, max_tokens: int, numbered: bool) -> ReadWindow:
    """The pure windowing with one token per character."""
    return take_read_window(lines, start=start, end=end, max_tokens=max_tokens, numbered=numbered, count=len)


def _fits_whole(prompt_content: str) -> None:
    """A read's whole result fits Largest command result, so the result cap leaves it alone."""
    limit = read_context_budget().result
    assert count_text_tokens(prompt_content) <= limit
    assert "were cut to fit" not in wrap_cli_tool_message(prompt_content)["content"]


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


# ── windowing, pure ───────────────────────────────────────────────────


def test_whole_lines_are_taken_while_they_fit_the_budget() -> None:
    window = _window(["aaaa", "bbbb", "cccc"], start=1, end=3, max_tokens=12, numbered=False)

    # Each line costs 4 + 1; a third would bring the total to 15.
    assert window == ReadWindow(first=1, last=2, requested_last=3, rendered=("aaaa", "bbbb"), cut=None)
    assert window.next_start == 3
    assert window.complete is False


def test_the_rendered_cost_counts_the_gutter_and_the_newline() -> None:
    lines = ["x", "y"]
    # "   1 | x" is 8 characters, plus 1 for its newline.
    one = _window(lines, start=1, end=2, max_tokens=17, numbered=True)
    both = _window(lines, start=1, end=2, max_tokens=18, numbered=True)

    assert one.rendered == ("   1 | x",)
    assert one.next_start == 2
    assert both.rendered == ("   1 | x", "   2 | y")
    assert both.complete is True


def test_an_over_long_first_line_is_cut_and_recorded() -> None:
    lines = ["y" * 50, "z"]

    # The longest prefix that fits with its newline: 19 of 20.
    numbered = _window(lines, start=1, end=2, max_tokens=20, numbered=True)
    assert numbered.rendered == ("   1 | " + "y" * 12,)
    assert numbered.cut == LineCut(line=1, total_chars=50, shown_chars=12)
    assert (numbered.last, numbered.next_start, numbered.complete) == (1, 2, False)

    bare = _window(lines, start=1, end=1, max_tokens=20, numbered=False)
    assert bare.rendered == ("y" * 19,)
    assert bare.cut == LineCut(line=1, total_chars=50, shown_chars=19)
    assert bare.next_start is None
    assert bare.complete is False

    below_gutter = _window(lines, start=1, end=1, max_tokens=3, numbered=True)
    assert below_gutter.rendered == ("  ",)
    assert below_gutter.cut == LineCut(line=1, total_chars=50, shown_chars=0)


def test_a_cut_is_found_by_tokens_not_characters() -> None:
    """With the real tokenizer the cut keeps the longest prefix whose tokens fit."""
    line = "word " * 400
    window = take_read_window([line, "next"], start=1, end=2, max_tokens=50, numbered=False, count=count_text_tokens)
    (shown,) = window.rendered
    assert count_text_tokens(shown + "\n") <= 50
    assert count_text_tokens(line[: len(shown) + 1] + "\n") > 50, "one more character would not fit"
    assert window.cut == LineCut(line=1, total_chars=len(line), shown_chars=len(shown))


def test_only_the_first_line_is_ever_cut() -> None:
    window = _window(["ab", "c" * 50], start=1, end=2, max_tokens=20, numbered=False)

    assert window.rendered == ("ab",)
    assert window.cut is None
    assert window.next_start == 2


def test_next_start_and_complete_at_the_boundaries() -> None:
    lines = ["abc", "def"]

    exactly = _window(lines, start=1, end=2, max_tokens=8, numbered=False)
    assert exactly.last == 2
    assert exactly.next_start is None
    assert exactly.complete is True

    one_over = _window(lines, start=1, end=2, max_tokens=7, numbered=False)
    assert one_over.last == 1
    assert one_over.next_start == 2
    assert one_over.complete is False

    last_line = _window(lines, start=2, end=2, max_tokens=1000, numbered=True)
    assert (last_line.first, last_line.last, last_line.requested_last) == (2, 2, 2)
    assert last_line.complete is True

    # A first line that fits exactly, its newline included, is shown whole, not cut.
    full_width = _window(["x" * 10, "y"], start=1, end=2, max_tokens=11, numbered=False)
    assert full_width.rendered == ("x" * 10,)
    assert full_width.cut is None
    assert full_width.next_start == 2


@pytest.mark.parametrize(
    ("lines", "start", "end", "max_tokens"),
    [
        (["a", "b"], 0, 1, 10),
        (["a", "b"], 2, 1, 10),
        (["a", "b"], 1, 3, 10),
        ([], 1, 1, 10),
        (["a", "b"], 1, 2, 0),
    ],
)
def test_a_bad_window_raises(lines: list[str], start: int, end: int, max_tokens: int) -> None:
    with pytest.raises(ValueError):
        _window(lines, start=start, end=end, max_tokens=max_tokens, numbered=True)


def test_read_on_note_wording_and_a_quoted_path() -> None:
    complete = _window(["a"], start=1, end=1, max_tokens=100, numbered=True)
    assert read_on_note(complete, path="/me/a.md", max_tokens=100) is None

    partial = _window(["aaaa"] * 5, start=1, end=5, max_tokens=10, numbered=False)
    note = read_on_note(partial, path="/me/my notes.md", max_tokens=10)
    assert note == (
        "Stopped after line 2 of 5: one read returns up to 10 tokens. "
        "Read on with: rr '/me/my notes.md' 3:5"
    )
    command = _READ_ON_RE.search(note).group(1)
    assert shlex.split(command) == ["rr", "/me/my notes.md", "3:5"]

    cut = _window(["y" * 40_000, "z"], start=1, end=2, max_tokens=30_000, numbered=True)
    assert read_on_note(cut, path="/me/min.js", max_tokens=30_000) == (
        "Line 1 has 40,000 characters; only the first 29,992 are shown.\n"
        "Stopped after line 1 of 2: one read returns up to 30,000 tokens. "
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
    _fits_whole(result.prompt_content)


def test_rr_over_the_budget_returns_a_prefix_and_reads_on_from_the_note() -> None:
    agent, state, me = _agent()
    lines = [f"line {n:02d}" for n in range(1, 41)]
    (me / "long.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _set_result_budget(200)

    first = execute_bm_cli(agent, state, "rr /me/long.txt 1:40")

    assert first.ok is True, first.prompt_content
    last = first.data["end"]
    assert first.data["start"] == 1 and 1 < last < 40
    assert first.data["lines"] == [{"line": n, "text": f"line {n:02d}"} for n in range(1, last + 1)]
    assert f"range: 1:{last}" in first.prompt_content
    assert f"{last + 1:>4} | line {last + 1:02d}" not in first.prompt_content
    assert first.detail.endswith(f"read lines 1:{last} from /me/long.txt")
    note = re.search(
        rf"Stopped after line {last} of 40: one read returns up to [\d,]+ tokens\. "
        rf"Read on with: rr /me/long\.txt {last + 1}:40",
        first.prompt_content,
    )
    assert note is not None
    assert note.start() > first.prompt_content.index(f"{last:>4} | line {last:02d}")
    _fits_whole(first.prompt_content)

    seen = [item["line"] for item in first.data["lines"]]
    prompt = first.prompt_content
    while (match := _READ_ON_RE.search(prompt)) is not None:
        follow = execute_bm_cli(agent, state, match.group(1))
        assert follow.ok is True, follow.prompt_content
        assert follow.data["start"] == seen[-1] + 1
        _fits_whole(follow.prompt_content)
        seen.extend(item["line"] for item in follow.data["lines"])
        prompt = follow.prompt_content
    assert seen == list(range(1, 41))


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
    lines = [f"row {n:02d}" for n in range(1, 61)]
    (me / "big.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _set_result_budget(200)

    result = execute_bm_cli(agent, state, "cat /me/big.txt")

    assert result.ok is True, result.prompt_content
    last = int(re.search(r"read lines 1:(\d+) of /me/big\.txt$", result.detail).group(1))
    assert 1 < last < 60
    shown = "\n".join(lines[:last])
    assert result.data["content"] == shown
    assert shown in result.prompt_content
    assert f"row {last + 1:02d}" not in result.prompt_content
    assert re.search(
        rf"Stopped after line {last} of 60: one read returns up to [\d,]+ tokens\. "
        rf"Read on with: rr /me/big\.txt {last + 1}:60",
        result.prompt_content,
    )
    assert "Read on with" not in result.data["content"]
    assert "..." not in result.prompt_content
    _fits_whole(result.prompt_content)


def test_a_one_line_file_longer_than_the_budget_is_cut_and_still_fits_whole() -> None:
    agent, state, me = _agent()
    (me / "min.js").write_text("var a=1;" * 2000, encoding="utf-8")
    _set_result_budget(200)

    result = execute_bm_cli(agent, state, "cat /me/min.js")

    assert result.ok is True, result.prompt_content
    assert "Line 1 has 16,000 characters; only the first" in result.prompt_content
    _fits_whole(result.prompt_content)


def test_a_result_budget_too_small_for_any_line_is_a_settings_error() -> None:
    agent, state, me = _agent()
    (me / "notes.md").write_text("alpha\nbeta\n", encoding="utf-8")
    _set_result_budget(20)

    with pytest.raises(config.ConfigError, match="Largest command result"):
        execute_bm_cli(agent, state, "cat /me/notes.md")


# ── settings ──────────────────────────────────────────────────────────


def test_the_old_character_read_limit_is_gone() -> None:
    """Reads are sized by Largest command result now; the old limit is pruned and refused."""
    client, headers = _client()
    assert query_one("SELECT value FROM settings WHERE key = 'cli_max_read_chars'") is None

    refused = client.put(
        "/api/settings/cli_max_read_chars", params={"value": "30000", "category": "advanced"}, headers=headers,
    )

    assert refused.status_code == 400
    assert "obsolete" in refused.json()["detail"]
    assert query_one("SELECT value FROM settings WHERE key = 'cli_max_read_chars'") is None


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
