"""HA-SEC-P1-03 — Telegram approval prefixes must match uniquely."""

from __future__ import annotations

from types import SimpleNamespace

from core.bm_cli.approvals import (
    display_approval_prefix,
    resolve_approval_by_unique_prefix,
)
from integrations.telegram.formatters import format_approval_list


def _req(request_id: str, command: str = "rm notes.md") -> SimpleNamespace:
    return SimpleNamespace(id=request_id, command=command, agent_id="agent-1")


def test_unique_prefix_still_matches() -> None:
    requests = [
        _req("aaaaaaaa-1111-4000-8000-000000000001"),
        _req("bbbbbbbb-2222-4000-8000-000000000002"),
    ]
    resolved = resolve_approval_by_unique_prefix("aaaa", requests)
    assert resolved.status == "unique"
    assert resolved.match_count == 1
    assert resolved.request is requests[0]


def test_shared_prefix_is_ambiguous() -> None:
    requests = [
        _req("abcdef01-1111-4000-8000-000000000001"),
        _req("abcdef99-2222-4000-8000-000000000002"),
    ]
    resolved = resolve_approval_by_unique_prefix("ab", requests)
    assert resolved.status == "ambiguous"
    assert resolved.match_count == 2
    assert resolved.request is None

    still_ambiguous = resolve_approval_by_unique_prefix("abcdef", requests)
    assert still_ambiguous.status == "ambiguous"

    unique = resolve_approval_by_unique_prefix("abcdef01", requests)
    assert unique.status == "unique"
    assert unique.request is requests[0]


def test_missing_and_empty_prefix_are_none() -> None:
    requests = [_req("aaaaaaaa-1111-4000-8000-000000000001")]
    assert resolve_approval_by_unique_prefix("zzzz", requests).status == "none"
    assert resolve_approval_by_unique_prefix("", requests).status == "none"
    assert resolve_approval_by_unique_prefix("   ", requests).status == "none"


def test_display_prefix_extends_past_eight_when_needed() -> None:
    ids = (
        "abcdef01-1111-4000-8000-000000000001",
        "abcdef99-2222-4000-8000-000000000002",
    )
    assert display_approval_prefix(ids[0], ids) == "abcdef01"
    assert display_approval_prefix(ids[1], ids) == "abcdef99"
    unique_ids = (
        "aaaaaaaa-1111-4000-8000-000000000001",
        "bbbbbbbb-2222-4000-8000-000000000002",
    )
    assert display_approval_prefix(unique_ids[0], unique_ids) == "aaaaaaaa"


def test_approval_list_shows_disambiguating_prefix() -> None:
    requests = [
        _req("abcdef01-1111-4000-8000-000000000001", "curl https://a.test"),
        _req("abcdef99-2222-4000-8000-000000000002", "curl https://b.test"),
    ]
    text = format_approval_list(requests, {})
    assert "abcdef01" in text
    assert "abcdef99" in text
