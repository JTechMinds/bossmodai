"""BossMod AI — Unified execution contract."""

from __future__ import annotations

import json

from core.bm_cli.contract import render_bm_cli_guidance


def default_action_contract_template() -> str:
    """Return the default authored execution contract template."""
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
        "  data.body = optional body text or manifest for cli commands that use it",
        "  data.out = durable work output text for work",
        "  data.to = message recipient kind for msg",
        "  data.aid = target agent id when an action needs another agent",
        "  data.msg = outward-facing message text for msg, or a short follow-up reply for done/block/deleg/drop",
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
        "  - cli: require data.cmd; include data.body only when the chosen command needs body text or a manifest",
        "  - cli + write: use data.body for a short exact file body, or omit data.body for one substantial generated file",
        "  - cli + append: require data.body and keep it small",
        "  - cli + bwrite: require data.body as a short manifest with path + goal entries, not full file contents",
        '  - cli + repsect: require data.body as the literal new section body; quote headings with spaces in data.cmd',
        '  - cli + rewsect: require data.body as a short rewrite goal; quote headings with spaces in data.cmd',
        "  - work: require data.out",
        '  - msg: require data.to and data.msg; require data.aid only when data.to="agent"',
        "  - assign: require data.aid plus data.task.title and data.task.desc; data.task.outs optional",
        "  - walk: require data.dst",
        '  - mtg: require data.mode; use mode="room" for in-person Meeting Room joins and mode="remote" for remote meetings',
        '  - mtg + mode="remote": require data.aid',
        "  - done: require data.sum; include data.msg when you should report completion back to the requester/owner now",
        "  - block / drop: require data.why; include data.msg when you should report the problem back now",
        "  - deleg: require data.aid; include data.msg when you should report the handoff back now",
        "  - ordinary coworker chat uses msg; durable agent-to-agent work uses assign",
        "  - if work is location-bound, walk first and work second",
        "  - if current deliverables require files, satisfy them with cli before done",
        "  - human-requested or manager-requested tasks should usually include a short natural data.msg when you finish, block, delegate, or abandon them",
        "  - do not invent keys that are not listed",
        "",
        render_bm_cli_guidance(),
        "",
        "EXAMPLES:",
        '  {"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
        '  {"act":"mtg","data":{"mode":"room","topic":"Planning"},"th":"join the meeting room session"}',
        '  {"act":"done","data":{"sum":"Draft saved.","msg":"Finished the draft and saved it. Want a short summary too?"},"th":"complete and report back"}',
    ]
    return "\n".join(lines)


def render_action_contract() -> str:
    """Render the unified prompt contract for execution turns."""
    return default_action_contract_template()


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
