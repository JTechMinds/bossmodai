"""The reply cap of every agent model call is the Reply budget, with no fallback number.

``client.completion()`` without ``max_tokens`` sends ``ContextBudget.reply``:
Reply % of the usable context window (core/context_budget.py). The old
``default_max_tokens`` setting and its silent ``or 16384`` are gone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.llm.client import completion
from db.settings import get_seed_setting_default
from tests._context_budget import set_context_budget

ROOT = Path(__file__).resolve().parents[1]


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


def _provider(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every provider request; each answers with one short reply."""
    sent: list[dict[str, Any]] = []

    async def _fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        sent.append(kwargs)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "model": kwargs["model"],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    return sent


async def _call(**kwargs: Any) -> None:
    await completion(
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
        api_base="http://127.0.0.1:9/v1",
        extra_body='{"stream": false}',
        connection_id="test-connection",
        **kwargs,
    )


async def test_without_max_tokens_the_reply_budget_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _provider(monkeypatch)
    await _call()
    # Medium, the seeded default: 12% of 116,000 usable tokens.
    assert sent[-1]["max_tokens"] == 13_920

    # Large: 12% of 244,000.
    assert set_context_budget(usable=244_000).reply == 29_280
    await _call()
    assert sent[-1]["max_tokens"] == 29_280


async def test_an_explicit_max_tokens_is_sent_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _provider(monkeypatch)
    await _call(max_tokens=512)
    assert sent[-1]["max_tokens"] == 512


async def test_invalid_context_settings_raise_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _provider(monkeypatch)
    db.execute("DELETE FROM settings WHERE key = 'context_reply_percent'")
    config.reload()
    with pytest.raises(config.ConfigError, match="context_reply_percent"):
        await _call()
    assert sent == [], "no provider call without a reply budget"


def test_default_max_tokens_is_gone() -> None:
    assert get_seed_setting_default("default_max_tokens") is None
    assert config.get("default_max_tokens") is None
    client_source = (ROOT / "core" / "llm" / "client.py").read_text(encoding="utf-8")
    assert "default_max_tokens" not in client_source
    assert "or 16384" not in client_source
    settings_js = (ROOT / "ui" / "static" / "js" / "settings" / "settings-system-meta.js").read_text(encoding="utf-8")
    assert "default_max_tokens" not in settings_js
