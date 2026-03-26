"""BossMod AI — Minimal authored prompt templating engine.

Supports a deliberately small template language:

- ``{{value}}``
- ``{{if path = 'literal'}} ... {{elseif path != 'literal'}} ... {{else}} ... {{end}}``

The engine is intentionally constrained. It does not support loops, function
calls, filters, arithmetic, or arbitrary expression evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


class TemplateError(ValueError):
    """Raised when a prompt template is invalid or cannot be rendered."""


@dataclass(frozen=True, slots=True)
class TextNode:
    text: str


@dataclass(frozen=True, slots=True)
class ValueNode:
    path: str


@dataclass(frozen=True, slots=True)
class Condition:
    path: str
    operator: str
    expected: str | None = None


@dataclass(frozen=True, slots=True)
class IfNode:
    branches: list[tuple[Condition, list[Any]]]
    else_nodes: list[Any]


_TAG_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_COND_RE = re.compile(
    r"^(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?:\s*(?P<op>!=|=)\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote))?$",
    re.DOTALL,
)


_ALIAS_PATHS = {
    "trigger": "trigger.type",
    "turn": "turn.contract_kind",
    "activity": "activity.kind",
    "task": "task.status",
    "channel": "channel.kind",
    "session": "session.kind",
}


def render_template(template: str, context: dict[str, Any], *, allowed_paths: set[str]) -> str:
    """Render one authored prompt template."""
    nodes = _parse_template(template, allowed_paths=allowed_paths)
    return "".join(_render_nodes(nodes, context))


def validate_template(template: str, *, allowed_paths: set[str]) -> None:
    """Raise ``TemplateError`` if one template is invalid."""
    _parse_template(template, allowed_paths=allowed_paths)


def syntax_guide() -> list[str]:
    """Return the supported template syntax examples for the UI."""
    return [
        "{{agent_name}}",
        "{{if trigger.type = 'human_chat'}} ... {{elseif trigger.type = 'task_assigned'}} ... {{else}} ... {{end}}",
        "{{if turn.contract_kind = 'decision'}} ... {{end}}",
    ]


def _parse_template(template: str, *, allowed_paths: set[str]) -> list[Any]:
    tokens: list[tuple[str, str]] = []
    last = 0
    for match in _TAG_RE.finditer(template):
        if match.start() > last:
            tokens.append(("text", template[last:match.start()]))
        tokens.append(("tag", match.group(1).strip()))
        last = match.end()
    if last < len(template):
        tokens.append(("text", template[last:]))

    nodes, index = _parse_nodes(tokens, 0, allowed_paths=allowed_paths, stop_tags=None)
    if index != len(tokens):
        raise TemplateError("Unexpected trailing template content")
    return nodes


def _parse_nodes(
    tokens: list[tuple[str, str]],
    index: int,
    *,
    allowed_paths: set[str],
    stop_tags: set[str] | None,
) -> tuple[list[Any], int]:
    nodes: list[Any] = []
    while index < len(tokens):
        token_type, value = tokens[index]
        if token_type == "text":
            nodes.append(TextNode(value))
            index += 1
            continue

        keyword = value.split(maxsplit=1)[0] if value else ""
        if stop_tags and keyword in stop_tags:
            return nodes, index
        if keyword in {"elseif", "else", "end"}:
            raise TemplateError(f"Unexpected '{{{{{keyword}}}}}' without matching '{{{{if}}}}'")
        if keyword == "if":
            node, index = _parse_if(tokens, index, allowed_paths=allowed_paths)
            nodes.append(node)
            continue

        path = _canonical_path(value)
        _require_allowed_path(path, allowed_paths)
        nodes.append(ValueNode(path))
        index += 1

    if stop_tags:
        expected = " / ".join(sorted(stop_tags))
        raise TemplateError(f"Missing closing template tag for block; expected one of: {expected}")
    return nodes, index


def _parse_if(
    tokens: list[tuple[str, str]],
    index: int,
    *,
    allowed_paths: set[str],
) -> tuple[IfNode, int]:
    tag = tokens[index][1]
    condition = _parse_condition(tag[3:].strip(), allowed_paths=allowed_paths)
    index += 1

    branches: list[tuple[Condition, list[Any]]] = []
    branch_nodes, index = _parse_nodes(tokens, index, allowed_paths=allowed_paths, stop_tags={"elseif", "else", "end"})
    branches.append((condition, branch_nodes))

    else_nodes: list[Any] = []
    while index < len(tokens):
        token_type, value = tokens[index]
        if token_type != "tag":
            raise TemplateError("Template parser entered an invalid state")
        keyword = value.split(maxsplit=1)[0]
        if keyword == "elseif":
            condition = _parse_condition(value[7:].strip(), allowed_paths=allowed_paths)
            index += 1
            branch_nodes, index = _parse_nodes(tokens, index, allowed_paths=allowed_paths, stop_tags={"elseif", "else", "end"})
            branches.append((condition, branch_nodes))
            continue
        if keyword == "else":
            index += 1
            else_nodes, index = _parse_nodes(tokens, index, allowed_paths=allowed_paths, stop_tags={"end"})
            if index >= len(tokens) or tokens[index][0] != "tag" or tokens[index][1] != "end":
                raise TemplateError("Missing '{{end}}' after '{{else}}'")
            index += 1
            return IfNode(branches=branches, else_nodes=else_nodes), index
        if keyword == "end":
            index += 1
            return IfNode(branches=branches, else_nodes=[]), index
        raise TemplateError(f"Unexpected template tag '{{{{{value}}}}}' inside conditional block")

    raise TemplateError("Missing '{{end}}' for conditional block")


def _parse_condition(expr: str, *, allowed_paths: set[str]) -> Condition:
    match = _COND_RE.match(expr)
    if not match:
        raise TemplateError(f"Invalid condition: {expr}")

    path = _canonical_path(match.group("path"))
    _require_allowed_path(path, allowed_paths)
    operator = match.group("op") or "truthy"
    expected = match.group("value")
    return Condition(path=path, operator=operator, expected=expected)


def _render_nodes(nodes: list[Any], context: dict[str, Any]) -> list[str]:
    rendered: list[str] = []
    for node in nodes:
        if isinstance(node, TextNode):
            rendered.append(node.text)
            continue
        if isinstance(node, ValueNode):
            rendered.append(_stringify_value(_resolve_value(context, node.path, exact=True), path=node.path))
            continue
        if isinstance(node, IfNode):
            matched = False
            for condition, branch_nodes in node.branches:
                if _evaluate_condition(condition, context):
                    rendered.extend(_render_nodes(branch_nodes, context))
                    matched = True
                    break
            if not matched and node.else_nodes:
                rendered.extend(_render_nodes(node.else_nodes, context))
            continue
        raise TemplateError("Unsupported template node")
    return rendered


def _evaluate_condition(condition: Condition, context: dict[str, Any]) -> bool:
    value = _resolve_value(context, condition.path, exact=False)
    if condition.operator == "truthy":
        return bool(value)
    if not isinstance(value, str):
        value = _stringify_value(value, path=condition.path)
    assert condition.expected is not None
    if condition.operator == "=":
        return value == condition.expected
    if condition.operator == "!=":
        return value != condition.expected
    raise TemplateError(f"Unsupported operator: {condition.operator}")


def _resolve_value(context: dict[str, Any], path: str, *, exact: bool) -> Any:
    parts = path.split(".")
    current: Any = context
    traversed: list[str] = []
    for part in parts:
        traversed.append(part)
        if not isinstance(current, dict) or part not in current:
            raise TemplateError(f"Unknown template variable: {path}")
        current = current[part]
    if exact and isinstance(current, dict):
        if "value" not in current:
            raise TemplateError(f"Template variable '{path}' does not resolve to printable text")
        return current["value"]
    return current


def _stringify_value(value: Any, *, path: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise TemplateError(f"Template variable '{path}' does not resolve to printable text")


def _canonical_path(path: str) -> str:
    candidate = path.strip()
    if candidate in _ALIAS_PATHS:
        candidate = _ALIAS_PATHS[candidate]
    if not _PATH_RE.match(candidate):
        raise TemplateError(f"Invalid template variable: {path}")
    return candidate


def _require_allowed_path(path: str, allowed_paths: set[str]) -> None:
    if path not in allowed_paths:
        raise TemplateError(f"Unsupported template variable: {path}")
