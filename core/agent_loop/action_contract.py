"""BossMod AI — Unified execution contract."""

from __future__ import annotations

import json

from core.bm_cli.contract import render_bm_cli_guidance


def render_action_contract() -> str:
    """Render the unified prompt contract for execution turns."""
    lines = [
        "EXECUTION TURN",
        "Return exactly one JSON object.",
        "Use the same schema for all resumed/internal actions.",
        "",
        "ALLOWED act VALUES:",
        "  cli | work | msg | assign | walk | mtg | idle | done | block | deleg | drop",
        "  cli=BossMod CLI, msg=send message, assign=delegate task,",
        "  mtg=join/start a meeting, done=complete, block=blocked, deleg=delegated, drop=abandoned",
        "",
        "REQUIRED JSON SHAPE:",
        "Do not output the schema itself. Output one JSON object matching this shape:",
        _render_action_shape(),
        "",
        "FIELD DEFINITIONS:",
        "  act = the next execution step you are taking",
        "  data = arguments for that execution step; only populate fields the chosen act needs",
        "  data.cmd = BossMod CLI command text for cli",
        "  data.body = optional body text for cli write/append commands",
        "  data.out = durable work output text for work",
        "  data.to = message recipient kind for msg",
        "  data.aid = target agent id when an action needs another agent",
        "  data.msg = outward-facing message text for msg",
        "  data.dst = destination for walk",
        "  data.mode = meeting mode for mtg",
        "  data.topic = optional meeting topic",
        "  data.sum = completion summary for done",
        "  data.why = blocking/abandon reason",
        "  data.task = delegated task payload for assign",
        "  th = short admin-visible note",
        "",
        "FIELD VALUES:",
        "  data.to = human | agent",
        "  data.dst = desk | meeting | break | main | south | hall",
        "  data.mode = room | remote",
        "",
        "RULES:",
        "  - cli: require data.cmd; data.body optional",
        "  - work: require data.out",
        "  - msg: require data.to and data.msg; require data.aid only when data.to=\"agent\"",
        "  - assign: require data.aid plus data.task.title and data.task.desc; data.task.outs optional",
        "  - walk: require data.dst",
        "  - mtg: require data.mode; use mode=\"room\" for in-person Meeting Room joins and mode=\"remote\" for remote meetings",
        "  - mtg + mode=\"remote\": require data.aid",
        "  - done: require data.sum",
        "  - block / drop: require data.why",
        "  - deleg: require data.aid",
        "  - ordinary coworker chat uses msg; durable agent-to-agent work uses assign",
        "  - if work is location-bound, walk first and work second",
        "  - if current deliverables require files, satisfy them with cli before done",
        "  - do not invent keys that are not listed",
        "",
        render_bm_cli_guidance(),
        "",
        "EXAMPLES:",
        '  {"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
        '  {"act":"mtg","data":{"mode":"room","topic":"Planning"},"th":"join the meeting room session"}',
        '  {"act":"msg","data":{"to":"human","msg":"Done. I saved it."},"th":"notify completion"}',
    ]
    return "\n".join(lines)


def _render_action_shape() -> str:
    """Render the actual model-facing JSON shape for execution turns."""
    shape = {
        "act": "cli | work | msg | assign | walk | mtg | idle | done | block | deleg | drop",
        "data": {
            "cmd": "string",
            "body": "string",
            "out": "string",
            "to": "human | agent",
            "aid": "string",
            "msg": "string",
            "dst": "desk | meeting | break | main | south | hall",
            "mode": "room | remote",
            "topic": "string",
            "sum": "string",
            "why": "string",
            "task": {
                "title": "string",
                "desc": "string",
                "outs": [{"type": "file", "path": "string", "desc": "string | null"}],
            },
        },
        "th": "string",
    }
    return "```json\n" + json.dumps(shape, indent=2) + "\n```"
