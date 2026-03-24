"""BossMod AI — Code-owned execution contract for resumed/internal turns."""

from __future__ import annotations

from dataclasses import dataclass

from core.bm_cli.contract import render_bm_cli_guidance


@dataclass(frozen=True)
class ActionSpec:
    """Prompt-visible description of a supported agent action."""

    name: str
    description: str
    example: str


_ACTION_SPECS = [
    ActionSpec(
        name="bm_cli",
        description="Query BossMod CLI for authoritative self/project information before choosing the next step.",
        example='{"action":"bm_cli","command":"status","thought":"check live status"}',
    ),
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
        description="Move your avatar to a destination while carrying out an existing commitment.",
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
    """Render the authoritative prompt contract for execution actions."""
    lines = [
        "This is an EXECUTION turn.",
        "You are carrying out an existing commitment or resumed activity in the virtual office.",
        "Respond with exactly one JSON action.",
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
            render_bm_cli_guidance(),
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
            "- Use message when the current activity requires a conversational reply.",
            "- If you need location-bound work, walk first and work second.",
            "- Use bm_cli for authoritative self/project facts when needed; do not treat old chat as runtime truth.",
            '- For bm_cli write commands, provide the file body in a separate "content" field.',
            '- If the current work contract includes deliverables such as files, satisfy them with bm_cli before complete.',
            '- Current work-contract file deliverables are stored as absolute BossMod CLI paths; write the required path exactly.',
            "- Use walkTo to fulfill an existing commitment, not to accept a new one.",
            '- "work", "complete", "blocked", "delegated", and "abandoned" act on the server-bound current task. Do not invent task IDs.',
            '- The "recipientType" field is required on message. Use "agentId" instead of agent names for agent-targeted actions.',
            '- "thought" is a brief admin-visible operational note, not hidden scratch reasoning.',
        ]
    )
    return "\n".join(lines)
