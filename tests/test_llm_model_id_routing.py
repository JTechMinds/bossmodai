"""BossMod AI — a connection's model id reaches the endpoint unchanged.

The configured model is the endpoint's exact id, as ``GET {base}/models``
lists it, and it may contain ``/``. litellm reads one leading segment as its
provider, so the client adds exactly one ``openai/`` whenever a base URL is
set. These tests use real litellm parsing, so an upgrade that changes prefix
handling fails here.
"""

from __future__ import annotations

import os
from pathlib import Path

import litellm
import pytest

import db
from core import config
from core.llm.client import canonicalize_openai_compatible_model
from tests._connections import TEST_BASE_URL


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


@pytest.mark.parametrize("raw", ["openai/gpt-5.6-sol", "z-ai/glm-5.2", "llama3", "Qwen-Flash"])
def test_configured_id_survives_litellm_provider_parsing(raw: str) -> None:
    routed = canonicalize_openai_compatible_model(raw, api_base=TEST_BASE_URL)
    assert litellm.get_llm_provider(model=routed, api_base=TEST_BASE_URL)[:2] == (raw, "openai")


async def test_completion_sends_a_slashed_id_through_the_openai_transport(monkeypatch) -> None:
    from core.llm.client import completion

    seen: dict = {}

    async def _fake_acompletion(**kwargs):
        seen.update(kwargs)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "model": kwargs["model"],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    result = await completion(
        model="openai/gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        api_base=TEST_BASE_URL,
        extra_body='{"stream": false}',
    )

    assert result.content == "ok"
    # litellm strips the one transport prefix; the endpoint gets openai/gpt-5.6-sol.
    assert seen["model"] == "openai/openai/gpt-5.6-sol"


def test_model_is_untouched_without_an_api_base() -> None:
    assert canonicalize_openai_compatible_model("gpt-4o", None) == "gpt-4o"
