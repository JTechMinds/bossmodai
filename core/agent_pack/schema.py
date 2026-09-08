"""Data-only agent pack schema ``bossmod.agent_pack/v1``.

YAML is parsed with SafeLoader and treated as a mapping of hire-contract
fields. Unknown keys are recorded and ignored. Dangerous keys (shell,
credentials, code execution) are rejected. Nothing in a pack is executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from core.agent_loop.role_contracts import suggest_finish_line
from core.models.agent import (
    HIRE_DESCRIPTION_MAX_LEN,
    HIRE_DONE_FAIL_BAR_MAX_LEN,
    HIRE_ROLE_MAX_LEN,
    Agent,
    normalize_hire_text,
)

SCHEMA_ID = "bossmod.agent_pack/v1"
MAX_PACK_BYTES = 65_536
PERSONALITY_HINT_MAX_LEN = HIRE_ROLE_MAX_LEN
TOOLS_HINT_MAX_ITEMS = 24
TOOLS_HINT_ITEM_MAX_LEN = 40

_CANONICAL_KEYS = (
    "schema",
    "specialty",
    "description",
    "what_done_looks_like",
    "personality_hint",
    "tools_hint",
)
_REQUIRED_FIELDS = ("specialty", "description", "what_done_looks_like")
_FIELD_ALIASES = {
    "role": "specialty",
    "done_fail_bar": "what_done_looks_like",
    "what-done-looks-like": "what_done_looks_like",
    "personality": "personality_hint",
    "tools": "tools_hint",
}
# Keys that look like they would run, install, or leak credentials.
# Nested copies are rejected too. Benign unknown keys are ignored.
_DANGEROUS_KEYS = frozenset({
    "api_key",
    "apikey",
    "bash",
    "cmd",
    "command",
    "commands",
    "credential",
    "credentials",
    "curl",
    "env",
    "environ",
    "environment",
    "eval",
    "exec",
    "execute",
    "execution",
    "hook",
    "hooks",
    "install",
    "on_import",
    "on_load",
    "password",
    "passwords",
    "payload",
    "private_key",
    "run",
    "runtime",
    "script",
    "scripts",
    "secret",
    "secrets",
    "setup",
    "sh",
    "shell",
    "ssh_key",
    "subprocess",
    "token",
    "tokens",
    "wget",
    "zsh",
})


class AgentPackError(ValueError):
    """Raised when a pack cannot be parsed, fetched, trusted, or exported."""

    def __init__(self, message: str, *, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class AgentPack:
    """Validated pack fields. Name and desk are never part of a pack."""

    schema: str
    specialty: str
    description: str
    what_done_looks_like: str
    personality_hint: str | None = None
    tools_hint: tuple[str, ...] = ()
    ignored_keys: tuple[str, ...] = field(default_factory=tuple)

    def hire_fields(self) -> dict[str, Any]:
        """Map pack fields onto POST /api/agents hire-contract keys.

        Does not include name, color, desk, seats, or credentials. The
        operator still names and seats the hire.
        """
        fields: dict[str, Any] = {
            "role": self.specialty,
            "description": self.description,
            "done_fail_bar": self.what_done_looks_like,
        }
        if self.personality_hint:
            fields["personality_hint"] = self.personality_hint
        if self.tools_hint:
            fields["tools_hint"] = list(self.tools_hint)
        return fields

    def as_dict(self) -> dict[str, Any]:
        """Canonical pack mapping for API responses (no ignored keys)."""
        data: dict[str, Any] = {
            "schema": self.schema,
            "specialty": self.specialty,
            "description": self.description,
            "what_done_looks_like": self.what_done_looks_like,
        }
        if self.personality_hint:
            data["personality_hint"] = self.personality_hint
        if self.tools_hint:
            data["tools_hint"] = list(self.tools_hint)
        return data

    def to_yaml(self) -> str:
        """Serialize a canonical pack. Data-only; no YAML tags."""
        dumped = yaml.safe_dump(
            self.as_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        if not dumped.endswith("\n"):
            dumped += "\n"
        return dumped


def parse_pack_yaml(raw: str | bytes) -> AgentPack:
    """Parse and validate a pack document. Never executes YAML content."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_PACK_BYTES:
            raise AgentPackError(
                "Pack exceeds the 64KiB size limit.",
                code="pack_too_large",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentPackError("Pack must be UTF-8 YAML.", code="invalid_yaml") from exc
    else:
        text = raw
        if len(text.encode("utf-8")) > MAX_PACK_BYTES:
            raise AgentPackError(
                "Pack exceeds the 64KiB size limit.",
                code="pack_too_large",
            )
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AgentPackError("Pack YAML is not valid data.", code="invalid_yaml") from exc
    if not isinstance(loaded, dict):
        raise AgentPackError(
            "Pack YAML must be a mapping of hire-contract fields.",
            code="invalid_schema",
        )
    _reject_dangerous_keys(loaded)
    _reject_non_data_values(loaded)
    return _pack_from_mapping(loaded)


def export_agent_pack(
    agent: Agent,
    *,
    personality_hint: str | None = None,
) -> AgentPack:
    """Build a pack from an existing agent's hire-contract profile fields."""
    specialty = normalize_hire_text(agent.role, max_len=HIRE_ROLE_MAX_LEN)
    description = normalize_hire_text(agent.description, max_len=HIRE_DESCRIPTION_MAX_LEN)
    if not specialty or not description:
        raise AgentPackError(
            "Pack export needs specialty and description on the agent profile.",
            code="export_incomplete",
            status=409,
        )
    done = normalize_hire_text(agent.done_fail_bar, max_len=HIRE_DONE_FAIL_BAR_MAX_LEN)
    if not done:
        done = suggest_finish_line(specialty, description)[:HIRE_DONE_FAIL_BAR_MAX_LEN]
    hint = normalize_hire_text(personality_hint, max_len=PERSONALITY_HINT_MAX_LEN)
    return AgentPack(
        schema=SCHEMA_ID,
        specialty=specialty,
        description=description,
        what_done_looks_like=done,
        personality_hint=hint,
    )


def _pack_from_mapping(loaded: dict[Any, Any]) -> AgentPack:
    normalized: dict[str, Any] = {}
    ignored: list[str] = []
    for raw_key, value in loaded.items():
        if not isinstance(raw_key, str):
            raise AgentPackError("Pack keys must be strings.", code="invalid_schema")
        key = _canonical_key(raw_key)
        if key is None:
            ignored.append(raw_key)
            continue
        if key in normalized and normalized[key] != value:
            raise AgentPackError(
                f"Pack field {key!r} is specified twice with different values.",
                code="invalid_schema",
            )
        normalized[key] = value

    schema = normalized.get("schema")
    if schema != SCHEMA_ID:
        raise AgentPackError(
            f"Pack schema must be {SCHEMA_ID!r}.",
            code="invalid_schema",
        )
    missing = [name for name in _REQUIRED_FIELDS if name not in normalized]
    if missing:
        raise AgentPackError(
            "Pack is missing required hire fields: " + ", ".join(missing) + ".",
            code="missing_field",
        )

    specialty = _required_text(normalized, "specialty", HIRE_ROLE_MAX_LEN)
    description = _required_text(normalized, "description", HIRE_DESCRIPTION_MAX_LEN)
    done = _required_text(normalized, "what_done_looks_like", HIRE_DONE_FAIL_BAR_MAX_LEN)
    personality_hint = _optional_text(
        normalized.get("personality_hint"),
        max_len=PERSONALITY_HINT_MAX_LEN,
        field_name="personality_hint",
    )
    tools_hint = _optional_tools_hint(normalized.get("tools_hint"))
    return AgentPack(
        schema=SCHEMA_ID,
        specialty=specialty,
        description=description,
        what_done_looks_like=done,
        personality_hint=personality_hint,
        tools_hint=tools_hint,
        ignored_keys=tuple(ignored),
    )


def _canonical_key(raw_key: str) -> str | None:
    folded = raw_key.strip()
    if folded in _FIELD_ALIASES:
        return _FIELD_ALIASES[folded]
    key = folded.replace("-", "_")
    if key in _CANONICAL_KEYS:
        return key
    return None


def _required_text(mapping: dict[str, Any], field_name: str, max_len: int) -> str:
    text = _optional_text(mapping.get(field_name), max_len=max_len, field_name=field_name)
    if not text:
        raise AgentPackError(
            f"Pack field {field_name!r} is required.",
            code="missing_field",
        )
    return text


def _optional_text(value: Any, *, max_len: int, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentPackError(
            f"Pack field {field_name!r} must be a string.",
            code="invalid_schema",
        )
    return normalize_hire_text(value, max_len=max_len)


def _optional_tools_hint(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise AgentPackError(
                    "Pack field 'tools_hint' must be a list of strings.",
                    code="invalid_schema",
                )
            stripped = item.strip()
            if stripped:
                items.append(stripped)
    else:
        raise AgentPackError(
            "Pack field 'tools_hint' must be a list of strings.",
            code="invalid_schema",
        )
    if len(items) > TOOLS_HINT_MAX_ITEMS:
        raise AgentPackError(
            "Pack field 'tools_hint' has too many entries.",
            code="invalid_schema",
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        if len(item) > TOOLS_HINT_ITEM_MAX_LEN:
            raise AgentPackError(
                "Pack field 'tools_hint' contains an oversized entry.",
                code="invalid_schema",
            )
        if not item.replace("_", "").isalnum() or not item[0].isalpha():
            raise AgentPackError(
                "Pack field 'tools_hint' entries must be simple tool names.",
                code="invalid_schema",
            )
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(key)
    return tuple(cleaned)


def _normalize_danger_key(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower().replace("-", "_")


def _reject_dangerous_keys(loaded: dict[Any, Any]) -> None:
    for key in _iter_keys(loaded):
        folded = _normalize_danger_key(key)
        if folded in _DANGEROUS_KEYS:
            raise AgentPackError(
                f"Pack key {key!r} is not allowed (data-only YAML; nothing is executed).",
                code="dangerous_key",
            )


def _iter_keys(value: Any) -> list[Any]:
    keys: list[Any] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            keys.append(child_key)
            keys.extend(_iter_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_iter_keys(child))
    return keys


def _reject_non_data_values(value: Any) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, dict):
        for child in value.values():
            _reject_non_data_values(child)
        return
    if isinstance(value, list):
        for child in value:
            _reject_non_data_values(child)
        return
    raise AgentPackError(
        "Pack YAML may only contain data (strings, numbers, booleans, lists, mappings).",
        code="invalid_yaml",
    )
