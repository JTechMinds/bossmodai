from __future__ import annotations

import re

import pytest

from core import config
from api.routes import (
    RuntimeContractPreviewBody,
    RuntimeContractTemplateOverridesBody,
    preview_runtime_contract as preview_runtime_contract_route,
)
from core.agent_loop.actions import parse_action
from core.agent_loop.decision_contract import parse_decision
from core.default_prompts import load_default_prompt, prompt_file_path
import db
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


def test_prompt_lint_reports_internal_runtime_names_in_override(isolated_db):
    report = lint_runtime_prompts(
        {
            "runtime_contract_decision": 'Use bm_cli for lookups. Return {"act":"reply","intent":"other","msg":"ok","th":"note"}.',
        }
    )

    assert report.ok is False
    assert report.status == "error"
    assert any(issue.code == "internal_bm_cli_name" for issue in report.issues)


def test_prompt_lint_reports_open_tasks_references_in_system_prompt_override(isolated_db):
    report = lint_runtime_prompts(
        {
            "system_prompt_template": "## Open Tasks\n{{pending_tasks}}\nTreat `Open Tasks` as active work.",
        }
    )

    assert report.ok is False
    assert report.status == "error"
    assert any(issue.code == "open_tasks_section_reference" for issue in report.issues)
    assert any(issue.code == "pending_tasks_placeholder_reference" for issue in report.issues)
    assert any(issue.code == "open_tasks_rule_reference" for issue in report.issues)


def test_prompt_lint_warns_when_saved_prompt_differs_from_shipped_default(isolated_db):
    db.set_setting(
        "runtime_contract_decision",
        load_default_prompt("runtime_contract_decision") + "\nCUSTOM NOTE",
        "advanced",
    )
    config.reload()

    report = lint_runtime_prompts()

    assert report.ok is False
    assert report.status == "warning"
    assert any(issue.code == "saved_prompt_differs_from_default" for issue in report.issues)
    mismatch_issue = next(issue for issue in report.issues if issue.code == "saved_prompt_differs_from_default")
    assert mismatch_issue.surface_key == "runtime_contract_decision"


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
    assert any(issue["code"] == "internal_bm_cli_name" for issue in payload["prompt_health"]["issues"])


@pytest.mark.asyncio
async def test_runtime_contract_payload_reports_saved_prompt_difference_as_warning(isolated_db):
    db.set_setting(
        "runtime_contract_decision",
        load_default_prompt("runtime_contract_decision") + "\nCUSTOM NOTE",
        "advanced",
    )
    config.reload()

    payload = await preview_runtime_contract_route(
        RuntimeContractPreviewBody(
            contract_kind="decision",
            trigger_type="human_chat",
            scope="bundle",
        )
    )

    assert payload["prompt_health"]["ok"] is False
    assert payload["prompt_health"]["status"] == "warning"
    assert any(issue["code"] == "saved_prompt_differs_from_default" for issue in payload["prompt_health"]["issues"])
