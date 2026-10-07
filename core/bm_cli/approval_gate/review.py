"""System AI review of one command, and the strict parse of its verdict.

The reviewer is a local reasoning model. Its replies arrive wrapped: a
leading ``<think>`` block, a markdown fence, or prose after the object.
Only that envelope is tolerated. The object itself is validated strictly
against :class:`ReviewVerdict`, and anything else fails closed to a card.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from core.bm_cli.approval_gate.context import ReviewContext
from core.bm_cli.approval_gate.facts import CommandFacts, PathFact
from core.default_prompts import render_default_prompt
from core.llm.system_completion import complete_text

logger = logging.getLogger(__name__)

REVIEW_PROMPT_KEY = "internal_cli_auto_approve_review"
REVIEW_UNREADABLE = "the reply was not a review"
REVIEW_FAILED = "the review call failed"
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_LOG_PREVIEW_CHARS = 200

Decision = Literal["approve", "ask"]
Basis = Literal[
    "harmless",
    "task_work",
    "boss_instruction",
    "precedent",
    "out_of_scope",
    "remote_mutation",
    "unsure",
]
_APPROVE_BASES = frozenset({"harmless", "task_work", "boss_instruction", "precedent"})


class ReviewVerdict(BaseModel):
    """One System AI verdict: approve or ask, the rubric basis, and why.

    Validation is strict: no extra keys, no type coercion, a non-blank
    ``why``, and an ``approve`` must name an approving basis (``harmless``,
    ``task_work``, ``boss_instruction`` or ``precedent``). A
    contradictory verdict is not a review.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Decision
    basis: Basis
    why: str

    @field_validator("why")
    @classmethod
    def _why_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("why must not be empty")
        return text

    @model_validator(mode="after")
    def _approve_names_an_approving_basis(self) -> ReviewVerdict:
        if self.decision == "approve" and self.basis not in _APPROVE_BASES:
            raise ValueError(f"approve cannot rest on basis {self.basis!r}")
        return self


@dataclass(frozen=True)
class ReviewOutcome:
    """The review result: a verdict, or the reason there is none.

    Attributes:
        verdict: The validated verdict, or None when ``error`` is set.
        error: Empty on success; otherwise an operator-facing reason.
    """

    verdict: ReviewVerdict | None
    error: str = ""


def review_command(facts: CommandFacts, context: ReviewContext) -> ReviewOutcome:
    """Ask System AI whether one approval-required command may run unattended.

    Args:
        facts: The command's deterministic facts.
        context: The work context (agent, task, conversation, precedents).

    Returns:
        A verdict, or an error outcome when the call failed, returned
        nothing, or returned something that is not a valid verdict. The
        caller turns every error into a card.

    Raises:
        TemplateError: The review prompt file is malformed.
    """
    messages = [
        {"role": "system", "content": render_default_prompt(REVIEW_PROMPT_KEY, {}, allowed_paths=set())},
        {"role": "user", "content": json.dumps(review_payload(facts, context), ensure_ascii=True)},
    ]
    try:
        raw = complete_text(messages)
    except Exception as exc:
        # Any failure here must fail closed to a card, and the card says so.
        logger.warning("auto-approve review call failed: %s: %s", type(exc).__name__, exc)
        return ReviewOutcome(verdict=None, error=REVIEW_FAILED)
    if raw is None:
        logger.warning("auto-approve review got no System AI reply")
        return ReviewOutcome(verdict=None, error=REVIEW_FAILED)
    verdict = parse_verdict(raw)
    if verdict is None:
        return ReviewOutcome(verdict=None, error=REVIEW_UNREADABLE)
    return ReviewOutcome(verdict=verdict)


def review_payload(facts: CommandFacts, context: ReviewContext) -> dict[str, Any]:
    """Return the JSON-ready user message: command facts plus work context.

    A script's facts are the union over its segments; ``segments`` then
    lists each one (command, effect, paths, write targets) so the reviewer
    sees which part does what.
    """
    payload: dict[str, Any] = {
        "command": facts.command,
        "cwd": facts.cwd_virtual,
        "effect": facts.effect,
        "paths": [_path_item(fact) for fact in facts.paths],
        "write_targets": [fact.label for fact in facts.write_targets],
    }
    if facts.segments:
        payload["segments"] = [
            {
                "command": segment.command,
                "effect": segment.effect,
                "paths": [_path_item(fact) for fact in segment.paths],
                "write_targets": [fact.label for fact in segment.write_targets],
            }
            for segment in facts.segments
        ]
    return {**payload, **asdict(context)}


def parse_verdict(raw: str | None) -> ReviewVerdict | None:
    """Parse a reviewer reply into a strict verdict, or None.

    Strips one leading ``<think>…</think>`` block, then a markdown fence,
    then decodes the first balanced JSON object starting at the first
    ``{`` (trailing prose is ignored) and validates it strictly.

    Args:
        raw: The model's reply text.

    Returns:
        The verdict, or None (logged at WARNING with the reason and the
        first characters of the reply) when the reply is not a valid one.
    """
    text = (raw or "").strip()
    if not text:
        return _unreadable("empty reply", raw)
    if text.startswith(_THINK_OPEN):
        end = text.find(_THINK_CLOSE)
        if end < 0:
            return _unreadable("unterminated <think> block", raw)
        text = text[end + len(_THINK_CLOSE):].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    start = text.find("{")
    if start < 0:
        return _unreadable("no JSON object", raw)
    try:
        payload, _end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as exc:
        return _unreadable(f"invalid JSON ({exc.msg})", raw)
    if not isinstance(payload, dict):
        return _unreadable("JSON value is not an object", raw)
    try:
        return ReviewVerdict.model_validate(payload)
    except ValidationError as exc:
        return _unreadable(f"invalid verdict ({exc.error_count()} errors)", raw)


def _path_item(fact: PathFact) -> dict[str, Any]:
    return {"label": fact.label, "workspace": fact.workspace, "is_root": fact.is_root}


def _unreadable(reason: str, raw: str | None) -> None:
    logger.warning(
        "auto-approve review reply rejected: %s; reply starts %r",
        reason,
        (raw or "")[:_LOG_PREVIEW_CHARS],
    )
    return None
