"""BossMod AI — Code-owned decision contract for direct requests."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


DecisionType = Literal["answer", "accept", "clarify", "decline", "defer"]
IntentKind = Literal[
    "question",
    "status_request",
    "meeting_request",
    "work_request",
    "relocation_request",
    "break_request",
    "social_request",
    "other",
]
CommitmentKind = Literal["none", "conversation", "meeting", "work", "break"]
Destination = Literal["desk", "meetingRoom", "breakRoom", "mainWorkspace", "southWorkspace", "hallway"]


class ConversationDecision(BaseModel):
    """Structured result for direct request handling."""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    intentKind: IntentKind
    reply: str | None = None
    commitmentKind: CommitmentKind = "none"
    destination: Destination | None = None
    title: str | None = None
    detail: str | None = None
    taskTitle: str | None = None
    taskDescription: str | None = None
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConversationDecision":
        if self.decision in {"answer", "clarify", "decline"} and self.commitmentKind != "none":
            raise ValueError(f'"{self.decision}" decisions must use commitmentKind="none"')
        if self.decision == "defer" and self.commitmentKind not in {"none", "work"}:
            raise ValueError('"defer" decisions may only defer work or keep commitmentKind="none"')
        if self.commitmentKind == "work" and self.decision == "accept" and not (self.taskTitle and self.taskTitle.strip()):
            raise ValueError('"accept" + commitmentKind="work" requires a non-empty "taskTitle"')
        if self.commitmentKind in {"meeting", "break"} and self.decision == "accept" and self.destination is None:
            raise ValueError(f'"accept" + commitmentKind="{self.commitmentKind}" requires "destination"')
        if self.commitmentKind == "break" and self.destination != "breakRoom":
            raise ValueError('break commitments must use destination="breakRoom"')
        return self


@dataclass(frozen=True)
class DecisionSpec:
    """Prompt-visible description of a supported direct-turn decision."""

    name: str
    description: str
    example: str


_DECISION_SPECS = [
    DecisionSpec(
        name="answer",
        description="Answer a question or provide status without changing commitments.",
        example='{"decision":"answer","intentKind":"status_request","reply":"I am finishing the API tests now.","commitmentKind":"none","thought":"status update"}',
    ),
    DecisionSpec(
        name="accept",
        description="Accept the request and create or replace the relevant commitment.",
        example='{"decision":"accept","intentKind":"meeting_request","reply":"I am heading to the meeting room now.","commitmentKind":"meeting","destination":"meetingRoom","title":"Project planning","detail":"Meet with the human operator in the meeting room.","thought":"accept the meeting"}',
    ),
    DecisionSpec(
        name="clarify",
        description="Ask a clarifying question before committing.",
        example='{"decision":"clarify","intentKind":"work_request","reply":"Do you want me to patch the API bug or just investigate it?","commitmentKind":"none","thought":"need scope clarification"}',
    ),
    DecisionSpec(
        name="decline",
        description="Decline the request and explain why.",
        example='{"decision":"decline","intentKind":"meeting_request","reply":"I cannot join right now because I am blocked on a critical task.","commitmentKind":"none","thought":"decline the request"}',
    ),
    DecisionSpec(
        name="defer",
        description="Acknowledge a work request or assignment but leave it pending for later.",
        example='{"decision":"defer","intentKind":"work_request","reply":"I captured it and will return to it after the current priority.","commitmentKind":"work","thought":"defer the new work"}',
    ),
]


def render_decision_contract() -> str:
    """Render the authoritative prompt contract for direct-request turns."""
    lines = [
        "This is a DIRECT REQUEST turn.",
        "Respond with exactly one JSON decision object.",
        "You are deciding how to respond and what commitment to make.",
        "Do not emit low-level execution actions like walkTo, work, attendMeeting, or remoteMeeting here.",
        "",
        "DECISIONS:",
    ]
    for spec in _DECISION_SPECS:
        lines.append(f"  {spec.name:<10} — {spec.description}")
    lines.extend(
        [
            "",
            "FIELDS:",
            "  decision        — answer | accept | clarify | decline | defer",
            "  intentKind      — question | status_request | meeting_request | work_request | relocation_request | break_request | social_request | other",
            "  reply           — the conversational reply to send back to the requester",
            "  commitmentKind  — none | conversation | meeting | work | break",
            "  destination     — desk | meetingRoom | breakRoom | mainWorkspace | southWorkspace | hallway",
            "  title/detail    — short commitment summary when useful",
            "  taskTitle/taskDescription — required when accepting new durable work from chat",
            "  thought         — brief admin-visible operational note",
            "",
            "RESPONSE FORMAT — respond with exactly ONE JSON object:",
        ]
    )
    for spec in _DECISION_SPECS:
        lines.append(f"  {spec.example}")
    lines.extend(
        [
            "",
            "RULES:",
            "- Valid JSON only, no markdown or extra text.",
            "- This contract is for direct requests only. Do not emit execution actions here.",
            "- Use decision=\"answer\" for pure status/question replies that do not change commitments.",
            "- Use decision=\"accept\" to create or replace a commitment.",
            "- Use commitmentKind=\"work\" only when a request becomes durable work.",
            "- When accepting work from chat, provide taskTitle and taskDescription.",
            "- When accepting a meeting or break, provide the destination.",
            "- decision=\"clarify\" and decision=\"decline\" must leave commitmentKind=\"none\".",
        ]
    )
    return "\n".join(lines)


def parse_decision(raw_response: str) -> dict[str, Any]:
    """Parse a direct-turn JSON decision from an LLM response."""
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse decision JSON: %s", text[:200])
                return {"decision": "_parse_failed", "thought": "", "_raw_snippet": text[:200]}
        else:
            logger.warning("No JSON found in decision response: %s", text[:200])
            return {"decision": "_parse_failed", "thought": "", "_raw_snippet": text[:200]}

    if not isinstance(parsed, dict):
        return {"decision": "_parse_failed", "thought": "", "_raw_snippet": "Decision payload must be a JSON object"}

    try:
        decision = ConversationDecision.model_validate(parsed)
    except ValidationError as exc:
        error = exc.errors()[0].get("msg", "Invalid decision payload")
        logger.warning("Invalid decision payload: %s", error)
        return {
            "decision": "_parse_failed",
            "thought": parsed.get("thought", "") if isinstance(parsed, dict) else "",
            "_raw_snippet": str(error)[:200],
        }

    return decision.model_dump()


def validate_decision_for_trigger(
    decision: ConversationDecision,
    *,
    trigger_type: str,
    active_task_id: str | None,
) -> str | None:
    """Validate a parsed decision against the turn context."""
    if trigger_type in {"human_chat", "peer_message"} and not (decision.reply and decision.reply.strip()):
        return 'direct requests require a non-empty "reply"'

    if trigger_type == "task_assigned":
        if decision.commitmentKind == "work" and decision.taskTitle:
            return 'task assignment decisions must not invent a new "taskTitle"'
        if decision.decision in {"answer", "clarify"}:
            return 'task assignment turns must accept, defer, or decline the assignment'

    if trigger_type in {"human_chat", "peer_message"} and decision.commitmentKind == "work":
        if decision.decision in {"accept", "defer"} and not (decision.taskTitle and decision.taskTitle.strip()):
            return 'direct work requests must provide a non-empty "taskTitle"'

    return None
