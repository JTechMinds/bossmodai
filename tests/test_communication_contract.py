"""Closed-enum communication contract: schema, defaults, role-prompt injection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.communication_contract import (
    AUDIENCE_VALUES,
    AUDITOR_DEFAULT,
    DENSITY_VALUES,
    JARGON_VALUES,
    PLANNER_DEFAULT,
    TONE_VALUES,
    CommunicationContractError,
    default_communication,
    parse_communication,
)
from core.agent_loop.role_contracts import format_role_contract_block
from core.agent_pack.schema import AgentPackError, parse_pack_yaml
from core.llm import context_builder
from tests.test_agent_packs import AUDITOR_PACK, PLANNER_PACK, THIN_PACK

ROOT = Path(__file__).resolve().parent.parent
COMM_JS = ROOT / "ui" / "static" / "js" / "core" / "communication.js"


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


def test_schema_rejects_communication_essay() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists.
communication: Be warm and chatty in long flowing paragraphs.
"""
        )
    assert exc.value.code == "invalid_schema"
    assert "closed enums" in str(exc.value)
    assert "essay" in str(exc.value)


def test_schema_rejects_unknown_communication_enum() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists.
communication:
  tone: Shakespearean
  density: scannable
  jargon: none
  audience: operator
"""
        )
    assert exc.value.code == "invalid_schema"
    assert "communication.tone" in str(exc.value)
    assert "precise-but-scannable" in str(exc.value)


def test_schema_fills_omitted_communication_from_specialty() -> None:
    auditor = parse_pack_yaml(AUDITOR_PACK)
    assert auditor.communication.as_dict() == AUDITOR_DEFAULT.as_dict()
    planner = parse_pack_yaml(PLANNER_PACK)
    assert planner.communication.as_dict() == PLANNER_DEFAULT.as_dict()
    engineer = parse_pack_yaml(THIN_PACK)
    assert engineer.communication.as_dict() == {
        "tone": "direct",
        "density": "compact",
        "jargon": "field",
        "audience": "implementer",
    }


def test_fixture_packs_ship_communication_defaults() -> None:
    auditor_text = (
        ROOT / "tests" / "fixtures" / "agent_mp" / "packs" / "engineering"
        / "code-auditor.agent.yaml"
    ).read_text(encoding="utf-8")
    planner_text = (
        ROOT / "tests" / "fixtures" / "agent_mp" / "packs" / "product"
        / "feature-planner.agent.yaml"
    ).read_text(encoding="utf-8")
    assert "Out of scope:" in auditor_text
    assert "Out of scope:" in planner_text
    assert "tone: precise-but-scannable" in auditor_text
    assert "tone: product-clear" in planner_text
    assert auditor_text.index("Out of scope:") < auditor_text.index("communication:")
    pack = parse_pack_yaml(auditor_text)
    assert "Live production deploys" in pack.description


def test_parse_communication_fills_partial_mapping() -> None:
    contract = parse_communication(
        {"tone": "warm"},
        specialty="Code Auditor",
    )
    assert contract.tone == "warm"
    assert contract.density == "scannable"
    assert contract.jargon == "field"
    assert contract.audience == "operator"


def test_communication_ignores_say_and_stays_voice_density() -> None:
    contract = parse_communication(
        {
            "tone": "direct",
            "density": "scannable",
            "say": "Done. Tests passed.",
            "actions": [],
        },
        specialty="Writer",
    )
    assert contract.as_dict() == {
        "tone": "direct",
        "density": "scannable",
        "jargon": "none",
        "audience": "operator",
    }
    assert "say" not in contract.as_dict()


def test_parse_communication_rejects_non_mapping() -> None:
    with pytest.raises(CommunicationContractError):
        parse_communication(["precise-but-scannable"])


def test_default_communication_matches_role_families() -> None:
    assert default_communication("Code Auditor") == AUDITOR_DEFAULT
    assert default_communication("Feature Planner") == PLANNER_DEFAULT
    assert default_communication("Writer").tone == "product-clear"
    assert default_communication(None) == default_communication("")


def test_role_contract_injects_communication_block() -> None:
    auditor = db.create_agent("Ada", role="Code Auditor")
    block = format_role_contract_block(auditor)
    assert "Communication:" in block
    assert "- tone: precise-but-scannable" in block
    assert "- audience: operator" in block
    planner = db.create_agent(
        "Kim",
        role="Feature Planner",
        communication=PLANNER_DEFAULT.as_dict(),
    )
    planned = format_role_contract_block(planner)
    assert "- tone: product-clear" in planned
    assert "- audience: mixed" in planned


def test_build_context_injects_communication_into_role_prompt() -> None:
    agent = db.create_agent("Remy", role="Code Auditor")
    state = db.get_agent_state(agent.id)
    assert state is not None
    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={
                "type": "channel_message",
                "source_channel": "channel",
                "content": "Review the claim.",
                "from_name": "Human Operator",
            },
            conversation_history=[],
            prompt_notifications=[],
            reference_materials=[],
            contract_kind="decision",
        )
    )
    role_msgs = [
        str(message.get("content") or "")
        for message in context
        if str(message.get("content") or "").startswith("# Role contract")
    ]
    assert role_msgs
    assert "Communication:" in role_msgs[0]
    assert "- tone: precise-but-scannable" in role_msgs[0]


def test_create_and_patch_agent_persist_communication() -> None:
    agent = db.create_agent(
        "Pat",
        role="Writer",
        communication={"tone": "warm", "density": "thorough", "jargon": "none", "audience": "operator"},
    )
    assert agent.communication == {
        "tone": "warm",
        "density": "thorough",
        "jargon": "none",
        "audience": "operator",
    }
    updated = db.update_agent(
        agent.id,
        communication={"tone": "direct", "density": "compact", "jargon": "light", "audience": "mixed"},
    )
    assert updated is not None
    assert updated.communication["tone"] == "direct"
    assert updated.communication["audience"] == "mixed"


def test_js_communication_enums_match_python() -> None:
    source = COMM_JS.read_text(encoding="utf-8")
    for value in sorted(TONE_VALUES | DENSITY_VALUES | JARGON_VALUES | AUDIENCE_VALUES):
        assert f"'{value}'" in source
    assert "precise-but-scannable" in source
    assert "product-clear" in source
    assert "function defaultFor(" in source
