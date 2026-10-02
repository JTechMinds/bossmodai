"""Test helper: give a test agent an AI connection for a model.

An agent routes through one AI connection (``agents.connection_id``), so a
test that wants an agent "on model X" creates a connection naming X and links
it. The base URL is a closed local port: no test may reach a real provider.
"""

from __future__ import annotations

import db

TEST_BASE_URL = "http://127.0.0.1:9/v1"


def model_connection(model: str, *, extra_body: str | None = None) -> str:
    """Create an AI connection for ``model`` and return its id."""
    return db.create_connection(
        name=f"Test {model}",
        api_base_url=TEST_BASE_URL,
        model=model,
        extra_body=extra_body,
    ).id
