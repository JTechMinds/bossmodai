"""Microsoft 365 Mailbox: new unread mail wakes the agent (wake.py over httpx.MockTransport)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from core.extensions.contract import ExtensionContext, SupportsWake
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
auth = importlib.import_module(f"{_PACKAGE.__name__}.auth")
graph = importlib.import_module(f"{_PACKAGE.__name__}.graph")
ids = importlib.import_module(f"{_PACKAGE.__name__}.ids")
wake = importlib.import_module(f"{_PACKAGE.__name__}.wake")

MAILBOX = "reports@contoso.com"
BASE = "https://graph.microsoft.com/v1.0"
ROOT = f"{BASE}/users/{MAILBOX}"
CONFIG = {"tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": MAILBOX}
AGENT = "agent-1"
T0 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


class FakeTokens:
    def acquire(self, tenant_id, client_id, secret):
        return auth.AccessToken(token="tok", expires_at=9e9)


class Inbox:
    """A Graph inbox answering the new-unread query from a message list."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.fail: httpx.Response | None = None

    def add(self, graph_id: str, at: datetime, *, sender: str = "alice@x.com", read: bool = False) -> None:
        self.messages.append({
            "id": graph_id, "subject": f"Subject {graph_id}",
            "from": {"emailAddress": {"name": "Alice Doe", "address": sender}},
            "receivedDateTime": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "isRead": read,
            "bodyPreview": "Hello   there", "hasAttachments": False,
        })

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return self.fail
        flt = request.url.params["$filter"]
        since = datetime.fromisoformat(flt.split("receivedDateTime ge ")[1].split(" ")[0].replace("Z", "+00:00"))
        found = [
            m for m in self.messages
            if not m["isRead"] and datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")) >= since
        ]
        found.sort(key=lambda m: m["receivedDateTime"])
        top = int(request.url.params["$top"])
        body: dict = {"value": found[:top]}
        if len(found) > top:
            body["@odata.nextLink"] = "https://elsewhere.example/next"
        return httpx.Response(200, json=body)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def env(tmp_path: Path):
    inbox = Inbox()
    config = {"value": dict(CONFIG)}
    ctx = ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: config["value"])
    extension = _PACKAGE.Ms365MailExtension(ctx, transport=httpx.MockTransport(inbox), tokens=FakeTokens())
    clock = Clock()
    # The instance's wake clock is the only "now"; swap it for a controllable one.
    extension._wake._clock = clock
    return type("Env", (), {"ext": extension, "inbox": inbox, "clock": clock, "config": config, "tmp": tmp_path})


def _state(env) -> dict:
    return json.loads((env.tmp / "wake" / f"{AGENT}.json").read_text(encoding="utf-8"))


def test_the_extension_implements_the_wake_contract(env) -> None:
    assert isinstance(env.ext, SupportsWake)
    assert _ENTRY.manifest.wake.interval_field == "check_interval_seconds"
    field = next(item for item in _ENTRY.manifest.agent_config.fields if item.key == "check_interval_seconds")
    assert (field.kind, field.min, field.max, field.default, field.required) == ("number", 15, 3600, "90", False)
    assert field.label == "Check for new mail every (seconds)"


def test_the_first_poll_records_since_and_returns_nothing(env) -> None:
    env.inbox.add("OLD-1", T0 - timedelta(hours=1))
    assert env.ext.poll_wake(AGENT) is None
    assert env.inbox.requests == []  # the backlog never wakes anyone
    assert _state(env) == {"mailbox": MAILBOX, "since": T0.isoformat(), "seen_at_since": []}


def test_a_new_unread_message_is_a_line_with_a_resolvable_short_id(env) -> None:
    env.ext.poll_wake(AGENT)
    env.inbox.add("NEW-1", T0 + timedelta(minutes=1))
    batch = env.ext.poll_wake(AGENT)
    assert batch is not None and batch.agent_id == AGENT
    assert batch.title == f"New email in {MAILBOX}"
    short = ids.short_id("NEW-1")
    assert batch.lines == [f'[{short}] Alice Doe <alice@x.com> — Subject NEW-1 — "Hello there"']
    id_map = ids.IdMap(env.tmp / "message_ids" / f"{AGENT}.json", 500)
    assert id_map.resolve(short) == ids.MessageRef(folder="inbox", graph_id="NEW-1")


def test_the_query_filters_unread_since_the_cursor_oldest_first_under_the_inbox(env) -> None:
    env.ext.poll_wake(AGENT)
    env.ext.poll_wake(AGENT)
    request = env.inbox.requests[0]
    assert str(request.url).startswith(ROOT + "/")
    assert request.url.path == "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages"
    assert request.url.params["$filter"] == "receivedDateTime ge 2026-09-29T09:00:00Z and isRead eq false"
    assert request.url.params["$orderby"] == "receivedDateTime asc"
    assert request.url.params["$top"] == "25"


def test_self_sent_mail_is_excluded(env) -> None:
    env.ext.poll_wake(AGENT)
    env.inbox.add("SELF-1", T0 + timedelta(minutes=1), sender="Reports@Contoso.com")
    assert env.ext.poll_wake(AGENT) is None
    # Moved past it directly, so a page of own mail cannot block real mail.
    assert _state(env)["seen_at_since"] == ["SELF-1"]
    env.inbox.add("NEW-2", T0 + timedelta(minutes=2))
    batch = env.ext.poll_wake(AGENT)
    assert len(batch.lines) == 1 and "Subject NEW-2" in batch.lines[0]


def test_the_cursor_is_unchanged_until_commit(env) -> None:
    env.ext.poll_wake(AGENT)
    before = _state(env)
    env.inbox.add("NEW-1", T0 + timedelta(minutes=1))
    first = env.ext.poll_wake(AGENT)
    assert _state(env) == before
    again = env.ext.poll_wake(AGENT)
    assert again.lines == first.lines  # not committed, so delivered again
    env.ext.commit_wake(first)
    assert _state(env) == {"mailbox": MAILBOX, "since": (T0 + timedelta(minutes=1)).isoformat(), "seen_at_since": ["NEW-1"]}
    assert env.ext.poll_wake(AGENT) is None


def test_equal_timestamp_ids_are_not_redelivered(env) -> None:
    env.ext.poll_wake(AGENT)
    at = T0 + timedelta(minutes=5)
    env.inbox.add("A", at)
    env.ext.commit_wake(env.ext.poll_wake(AGENT))
    env.inbox.add("B", at)  # same second, arrived after the first commit
    batch = env.ext.poll_wake(AGENT)
    assert len(batch.lines) == 1 and "Subject B" in batch.lines[0]
    env.ext.commit_wake(batch)
    assert _state(env)["seen_at_since"] == ["A", "B"]
    assert env.ext.poll_wake(AGENT) is None


def test_a_mailbox_change_resets_the_state(env) -> None:
    env.ext.poll_wake(AGENT)
    env.inbox.add("NEW-1", T0 + timedelta(minutes=1))
    env.config["value"] = {**CONFIG, "mailbox": "other@contoso.com"}
    env.clock.now = T0 + timedelta(minutes=10)
    assert env.ext.poll_wake(AGENT) is None
    assert _state(env) == {"mailbox": "other@contoso.com", "since": (T0 + timedelta(minutes=10)).isoformat(), "seen_at_since": []}


def test_skip_wake_moves_to_now_without_a_graph_call(env) -> None:
    env.ext.poll_wake(AGENT)
    env.inbox.add("VACATION-1", T0 + timedelta(minutes=1))
    env.clock.now = T0 + timedelta(hours=2)
    env.ext.skip_wake(AGENT)
    assert env.inbox.requests == []
    assert _state(env) == {"mailbox": MAILBOX, "since": (T0 + timedelta(hours=2)).isoformat(), "seen_at_since": []}
    assert env.ext.poll_wake(AGENT) is None  # vacation mail never wakes


def test_paging_continues_from_the_advanced_cursor(env) -> None:
    env.ext.poll_wake(AGENT)
    for n in range(30):
        env.inbox.add(f"N-{n:02d}", T0 + timedelta(minutes=n + 1))
    first = env.ext.poll_wake(AGENT)
    assert len(first.lines) == 25
    env.ext.commit_wake(first)
    second = env.ext.poll_wake(AGENT)
    assert [line.split("Subject ")[1].split(" ")[0] for line in second.lines] == [f"N-{n:02d}" for n in range(25, 30)]


def test_the_state_write_is_atomic(env, monkeypatch) -> None:
    env.ext.poll_wake(AGENT)
    before = (env.tmp / "wake" / f"{AGENT}.json").read_text(encoding="utf-8")
    env.inbox.add("NEW-1", T0 + timedelta(minutes=1))
    batch = env.ext.poll_wake(AGENT)

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(wake.os, "replace", refuse)
    with pytest.raises(OSError):
        env.ext.commit_wake(batch)
    assert (env.tmp / "wake" / f"{AGENT}.json").read_text(encoding="utf-8") == before


def test_a_corrupt_state_file_is_an_error_not_a_fresh_start(env) -> None:
    (env.tmp / "wake").mkdir()
    (env.tmp / "wake" / f"{AGENT}.json").write_text("{", encoding="utf-8")
    with pytest.raises(wake.WakeStateError):
        env.ext.poll_wake(AGENT)


def test_graph_errors_propagate_and_describe_as_plain_sentences(env) -> None:
    env.ext.poll_wake(AGENT)
    env.inbox.fail = httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "raw"}})
    with pytest.raises(graph.GraphHttpError) as caught:
        env.ext.poll_wake(AGENT)
    assert env.ext.describe_wake_error(caught.value) == "The app doesn't have permission to open this mailbox."
    assert env.ext.describe_wake_error(auth.GraphUnreachable("dns")) == graph.CONFIG_UNREACHABLE
    assert env.ext.describe_wake_error(wake.WakeStateError("x")) == graph.CONFIG_FAILED


def test_an_unconfigured_agent_is_an_explicit_error(env) -> None:
    env.config["value"] = None
    with pytest.raises(wake.WakeNotConfigured):
        env.ext.poll_wake(AGENT)
    with pytest.raises(wake.WakeNotConfigured):
        env.ext.skip_wake(AGENT)
