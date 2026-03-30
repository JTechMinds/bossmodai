from __future__ import annotations

import re

import pytest

from api.routes import (
    RuntimeContractPreviewBody,
    RuntimeContractTemplateOverridesBody,
    preview_runtime_contract as preview_runtime_contract_route,
)
from core.agent_loop.actions import parse_action
from core.agent_loop.decision_contract import parse_decision
from core.default_prompts import prompt_file_path
from core.prompting.runtime_prompt_lint import lint_runtime_prompts
from tests.test_agent_runtime import isolated_db


def test_runtime_prompt_health_default_prompts_are_clean(isolated_db):
    report = lint_runtime_prompts()

    assert report.ok is True
    assert report.status == "clean"
    assert report.issues == ()


@pytest.mark.asyncio
async def test_preview_runtime_contract_bundle_renders_full_prompt_bundle(isolated_db):
    payload = await preview_runtime_contract_route(
        RuntimeContractPreviewBody(
            contract_kind="decision",
            trigger_type="human_chat",
            scope="bundle",
        )
    )

    assert payload["scope"] == "bundle"
    assert payload["messages"][0]["role"] == "system"
    assert any(message["role"] == "user" for message in payload["messages"])
    assert "[SYSTEM 1]" in payload["rendered"]
    assert "[USER 1]" in payload["rendered"]
    assert "CONVERSATION TURN" in payload["rendered"]
    assert "AUTHORITATIVE COMMUNICATION SNAPSHOT" in payload["rendered"]
    assert payload["prompt_health"]["ok"] is True


def test_prompt_lint_reports_legacy_tokens_in_override(isolated_db):
    report = lint_runtime_prompts(
        {
            "runtime_contract_decision": 'Use bm_cli for lookups. Return {"act":"reply","intent":"other","msg":"ok","th":"note"}.',
        }
    )

    assert report.ok is False
    assert report.status == "error"
    assert any(issue.code == "legacy_bm_cli" for issue in report.issues)


def test_runtime_contract_decision_examples_parse(isolated_db):
    text = prompt_file_path("runtime_contract_decision").read_text(encoding="utf-8")
    example_section = text.split("EXAMPLES", maxsplit=1)[1]
    examples = re.findall(r"```json\n(.*?)\n```", example_section, flags=re.DOTALL)

    assert examples
    for example in examples:
        parsed = parse_decision(example)
        assert parsed["decision"] != "_parse_failed", example


def test_runtime_contract_execution_examples_parse(isolated_db):
    text = prompt_file_path("runtime_contract_execution").read_text(encoding="utf-8")
    example_section = text.split("EXAMPLES:", maxsplit=1)[1]
    examples = re.findall(r'^\s*(\{"act":.*\})\s*$', example_section, flags=re.MULTILINE)

    assert examples
    for example in examples:
        parsed = parse_action(example)
        assert parsed["action"] != "_parse_failed", example


@pytest.mark.asyncio
async def test_preview_bundle_lints_unsaved_overrides(isolated_db):
    payload = await preview_runtime_contract_route(
        RuntimeContractPreviewBody(
            contract_kind="decision",
            trigger_type="human_chat",
            scope="bundle",
            templates=RuntimeContractTemplateOverridesBody(
                decision='Legacy prompt says use bm_cli first.',
            ),
        )
    )

    assert payload["prompt_health"]["ok"] is False
    assert any(issue["code"] == "legacy_bm_cli" for issue in payload["prompt_health"]["issues"])
