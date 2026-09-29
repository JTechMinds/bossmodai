"""Browser Vision: recognising a bot check (R34) and the site cooldown file."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("browser-vision")
_PACKAGE = import_package(_ENTRY)
_botwall = importlib.import_module(f"{_PACKAGE.__name__}.botwall")
_cooldowns = importlib.import_module(f"{_PACKAGE.__name__}.cooldowns")
_commands = importlib.import_module(f"{_PACKAGE.__name__}.commands")

RULES = _commands.BrowserVisionDefaults.model_validate(_ENTRY.manifest.defaults).botwall


def test_the_manifest_rules_are_the_planned_defaults() -> None:
    assert RULES.statuses == (403, 429)
    assert "challenges.cloudflare.com" in RULES.frame_markers
    assert "are you a robot" in RULES.text_patterns
    assert (RULES.text_chars, RULES.cooldown_minutes) == (3000, 30)


@pytest.mark.parametrize("status", [403, 429])
def test_a_blocking_status_alone_is_a_wall(status: int) -> None:
    assert _botwall.detect(status, [], "Home", "Welcome", RULES) == f"HTTP {status}"


def test_a_challenge_frame_alone_is_a_wall() -> None:
    frames = ["https://example.com/ad", "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile"]
    assert _botwall.detect(200, frames, "Home", "Welcome", RULES) == "challenge frame from challenges.cloudflare.com"


def test_challenge_wording_in_the_title_or_text_alone_is_a_wall() -> None:
    assert _botwall.detect(None, [], "Are You a Robot?", "", RULES) == 'the page says "are you a robot"'
    assert _botwall.detect(200, [], "Redfin", "Please\n  verify you are   human", RULES) == (
        'the page says "verify you are human"'
    )


def test_wording_past_the_searched_length_is_ignored() -> None:
    text = "x" * RULES.text_chars + " are you a robot"
    assert _botwall.detect(200, [], "Page", text, RULES) is None


def test_a_normal_page_about_robots_is_not_a_wall() -> None:
    text = "Robot vacuum review: we tested 12 robot vacuums. The best robot for pet hair is…"
    assert _botwall.detect(200, ["https://www.youtube.com/embed/abc"], "Robot vacuum review", text, RULES) is None


def test_blank_rule_entries_are_rejected_at_load() -> None:
    raw = dict(_ENTRY.manifest.defaults)
    raw["botwall"] = {**raw["botwall"], "text_patterns": ["are you a robot", " "]}
    with pytest.raises(ValidationError, match="entries must not be blank"):
        _commands.BrowserVisionDefaults.model_validate(raw)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_a_cooldown_covers_the_site_and_its_subdomains_until_it_expires(tmp_path: Path) -> None:
    clock = _Clock()
    path = tmp_path / "cooldowns.json"
    store = _cooldowns.CooldownStore(path, clock=clock)
    blocked = store.block("redfin.com", 30)
    assert blocked.until - blocked.blocked_at == timedelta(minutes=30)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "redfin.com": {"blocked_at": "2026-09-28T22:30:00Z", "until": "2026-09-28T23:00:00Z"},
    }

    # A new store over the same file (a new process) sees it.
    reread = _cooldowns.CooldownStore(path, clock=clock)
    assert reread.active("redfin.com") == blocked
    assert reread.active("ratelimited.redfin.com") == blocked
    assert reread.active("notredfin.com") is None

    clock.now += timedelta(minutes=30)
    assert reread.active("redfin.com") is None
    # The expired entry is dropped from the file at the next write.
    reread.block("zillow.com", 30)
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["zillow.com"]


def test_a_malformed_cooldown_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "cooldowns.json"
    path.write_text(json.dumps({"redfin.com": "2026-09-28T23:00:00Z"}), encoding="utf-8")
    with pytest.raises(_cooldowns.CooldownStoreError):
        _cooldowns.CooldownStore(path).active("redfin.com")
