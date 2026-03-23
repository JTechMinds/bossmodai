"""BossMod AI — Code-owned action contract for agent turns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    """Prompt-visible description of a supported agent action."""

    name: str
    description: str
    example: str


_ACTION_SPECS = [
    ActionSpec(
        name="work",
        description="Create durable work output. Only use after moving to a workspace.",
        example='{"action":"work","output":"your work product","thought":"reasoning"}',
    ),
    ActionSpec(
        name="message",
        description="Send a direct message to the human operator or another agent using the explicit recipient contract.",
        example='{"action":"message","recipientType":"human","content":"message text","thought":"reasoning"}',
    ),
    ActionSpec(
        name="walkTo",
        description="Move your avatar to a destination before doing location-bound work.",
        example='{"action":"walkTo","destination":"desk","thought":"reasoning"}',
    ),
    ActionSpec(
        name="attendMeeting",
        description="Attend an in-person meeting from the meetingRoom. Optionally include agentId when meeting with another agent.",
        example='{"action":"attendMeeting","topic":"topic","thought":"reasoning"}',
    ),
    ActionSpec(
        name="remoteMeeting",
        description="Start a remote meeting from your current workspace.",
        example='{"action":"remoteMeeting","agentId":"agent-id","topic":"topic","thought":"reasoning"}',
    ),
    ActionSpec(
        name="startTask",
        description="Create and activate a new durable task from a direct assignment before you begin the work.",
        example='{"action":"startTask","title":"task title","description":"task details","thought":"reasoning"}',
    ),
    ActionSpec(
        name="resumeTask",
        description="Resume the latest pending task after an interruption or meeting.",
        example='{"action":"resumeTask","thought":"reasoning"}',
    ),
    ActionSpec(
        name="idle",
        description="You have nothing else to do right now.",
        example='{"action":"idle","thought":"reasoning"}',
    ),
    ActionSpec(
        name="complete",
        description="Mark the current task as complete and provide a short summary.",
        example='{"action":"complete","summary":"what was done","thought":"reasoning"}',
    ),
    ActionSpec(
        name="blocked",
        description="Mark the current task blocked and explain why.",
        example='{"action":"blocked","reason":"why blocked","thought":"reasoning"}',
    ),
    ActionSpec(
        name="delegated",
        description="Hand the current task to another agent.",
        example='{"action":"delegated","agentId":"agent-id","thought":"reasoning"}',
    ),
    ActionSpec(
        name="abandoned",
        description="Abandon the current task and explain why.",
        example='{"action":"abandoned","reason":"why abandoned","thought":"reasoning"}',
    ),
]

_DESTINATIONS = "desk, meetingRoom, breakRoom, mainWorkspace, southWorkspace, hallway"


def render_action_contract() -> str:
    """Render the authoritative prompt contract for agent actions."""
    lines = [
        "You are an AI agent in a virtual office. You control an avatar that represents your physical presence.",
        "Each turn you must respond with exactly one JSON action.",
        "",
        "ACTIONS:",
    ]
    for spec in _ACTION_SPECS:
        lines.append(f"  {spec.name:<14} — {spec.description}")
    lines.extend(
        [
            "",
            "DESTINATIONS (for walkTo):",
            f"  {_DESTINATIONS}",
            "",
            "RECIPIENT CONTRACT:",
            '  message to human: {"action":"message","recipientType":"human","content":"message text","thought":"reasoning"}',
            '  message to agent: {"action":"message","recipientType":"agent","agentId":"agent-id","content":"message text","thought":"reasoning"}',
            '  remoteMeeting/delegated: use the exact "agentId" from TEAM DIRECTORY.',
            '  attendMeeting: you may include "agentId" when the in-person meeting is with another agent.',
            "",
            "RESPONSE FORMAT — respond with exactly ONE JSON object:",
        ]
    )
    for spec in _ACTION_SPECS:
        lines.append(f"  {spec.example}")
    lines.extend(
        [
            "",
            "RULES:",
            "- Valid JSON only, no markdown or extra text.",
            "- Use message when you need to reply to the human operator.",
            "- If you need location-bound work, walk first and work second.",
            '- Use "startTask" when a direct conversation becomes a durable assignment.',
            '- Use "resumeTask" after an interruption when you should return to pending work.',
            '- "work", "complete", "blocked", "delegated", and "abandoned" act on the server-bound current task. Do not invent task IDs.',
            '- The "recipientType" field is required on message. Use "agentId" instead of agent names for agent-targeted actions.',
            '- "thought" is a brief admin-visible operational note, not hidden scratch reasoning.',
        ]
    )
    return "\n".join(lines)
