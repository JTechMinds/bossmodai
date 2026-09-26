"""Fake System AI answers for the numbered channel-router prompt.

The router prompt numbers members ``1…N`` per call and never shows agent
ids. A fake completion therefore has to read the Members roster of the
prompt it was handed to turn the agent ids a test wants into numbers,
exactly as the model would.
"""

from __future__ import annotations

import json

import db


def router_numbers(messages: list[dict[str, str]]) -> dict[str, int]:
    """Map member display name to its per-call number from the prompt's Members block."""
    user = next(item["content"] for item in messages if item.get("role") == "user")
    block = user.split("Members:\n", 1)[1].split("\n\n", 1)[0]
    numbers: dict[str, int] = {}
    for line in block.splitlines():
        number, name = [part.strip() for part in line.split(" | ")[:2]]
        if name in numbers:
            raise AssertionError(f"duplicate member name on the router roster: {name}")
        numbers[name] = int(number)
    return numbers


def number_for(messages: list[dict[str, str]], agent_id: str) -> int:
    """Return the number the prompt gave ``agent_id``. Fails if it is not a member."""
    agent = db.get_agent(agent_id)
    if agent is None:
        raise AssertionError(f"unknown agent id in router fake: {agent_id}")
    numbers = router_numbers(messages)
    if agent.name not in numbers:
        raise AssertionError(f"{agent.name} is not on the router roster: {sorted(numbers)}")
    return numbers[agent.name]


def speak_reply(messages: list[dict[str, str]], speak_ids: list[str]) -> str:
    """JSON ``{"speak": [numbers]}`` naming ``speak_ids`` in order."""
    return json.dumps({"speak": [number_for(messages, agent_id) for agent_id in speak_ids]})


def route_reply(messages: list[dict[str, str]], reply: list[str] | str) -> str:
    """A list is the speak ids to answer with; a string is raw completion text."""
    if isinstance(reply, str):
        return reply
    return speak_reply(messages, reply)
