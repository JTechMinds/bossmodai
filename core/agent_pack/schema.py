"""Data-only agent pack schema ``bossmod.agent_pack/v1``.

YAML is parsed with SafeLoader and treated as a mapping of hire-contract
fields. Unknown keys are recorded and ignored. Dangerous keys (shell,
credentials, code execution) are rejected. Nothing in a pack is executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import yaml

from core.agent_loop.specialty import suggest_finish_line
from core.agent_pack.sections import (
    DESCRIPTION_SECTION_KEYS,
    compose_labeled_description,
    compose_labeled_done,
    extract_labeled_sections,
)
from core.models.agent import (
    HIRE_DESCRIPTION_MAX_LEN,
    HIRE_DONE_FAIL_BAR_MAX_LEN,
    HIRE_ROLE_MAX_LEN,
    Agent,
    normalize_hire_text,
)

SCHEMA_ID = "bossmod.agent_pack/v1"
PACK_KIND_AGENT = "agent"
# Reserved for later pack types. v1 implements agent packs only.
RESERVED_PACK_KINDS = frozenset({"agent", "skill", "workflow"})
MAX_PACK_BYTES = 65_536
PERSONALITY_HINT_MAX_LEN = HIRE_ROLE_MAX_LEN
TOOLS_HINT_MAX_ITEMS = 24
TOOLS_HINT_ITEM_MAX_LEN = 40
PACK_AUTHOR_NAME_MAX_LEN = 80
PACK_AUTHOR_URL_MAX_LEN = 500
_STRUCTURED_FIELDS = DESCRIPTION_SECTION_KEYS + ("fail_examples",)

_CANONICAL_KEYS = (
    "schema",
    "kind",
    "pack_author",
    "specialty",
    "description",
    "what_done_looks_like",
    "personality_hint",
    "tools_hint",
    "mission",
    "in_scope",
    "out_of_scope",
    "handoff",
    "fail_examples",
)
_REQUIRED_FIELDS = ("specialty", "description", "what_done_looks_like")
_FIELD_ALIASES = {
    "role": "specialty",
    "done_fail_bar": "what_done_looks_like",
    "what-done-looks-like": "what_done_looks_like",
    "personality": "personality_hint",
    "tools": "tools_hint",
    "in-scope": "in_scope",
    "out-of-scope": "out_of_scope",
    "fail-examples": "fail_examples",
    "fail_example": "fail_examples",
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
class PackAuthor:
    """Optional pack authorship. Not a hire and never creates an agent."""

    name: str
    url: str | None = None

    def as_dict(self) -> dict[str, str]:
        data = {"name": self.name}
        if self.url:
            data["url"] = self.url
        return data


@dataclass(frozen=True)
class AgentPack:
    """Validated pack fields. Name and desk are never part of a pack."""

    schema: str
    kind: str
    specialty: str
    description: str
    what_done_looks_like: str
    personality_hint: str | None = None
    tools_hint: tuple[str, ...] = ()
    pack_author: PackAuthor | None = None
    ignored_keys: tuple[str, ...] = field(default_factory=tuple)

    def hire_fields(self) -> dict[str, Any]:
        """Map pack fields onto POST /api/agents hire-contract keys.

        Does not include name, color, desk, seats, credentials, or
        ``pack_author``. The operator still names and seats the hire.
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
            "kind": self.kind,
        }
        if self.pack_author:
            data["pack_author"] = self.pack_author.as_dict()
        data["specialty"] = self.specialty
        data["description"] = self.description
        data["what_done_looks_like"] = self.what_done_looks_like
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
    pack_author: PackAuthor | None = None,
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
        kind=PACK_KIND_AGENT,
        specialty=specialty,
        description=description,
        what_done_looks_like=done,
        personality_hint=hint,
        pack_author=pack_author,
    )


def pack_author_from_company(
    name: str | None,
    url: str | None = None,
) -> PackAuthor | None:
    """Build pack_author from company settings, or None if the name is unknown."""
    cleaned_name = normalize_hire_text(name, max_len=PACK_AUTHOR_NAME_MAX_LEN)
    if not cleaned_name:
        return None
    cleaned_url: str | None = None
    if url and str(url).strip():
        try:
            cleaned_url = _parse_author_url(url)
        except AgentPackError:
            cleaned_url = None
    return PackAuthor(name=cleaned_name, url=cleaned_url)


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
    kind = _parse_kind(normalized.get("kind"))
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
    pack_author, author_ignored = _optional_pack_author(normalized.get("pack_author"))
    ignored.extend(author_ignored)
    additive = {
        key: _optional_text(
            normalized.get(key),
            max_len=HIRE_DESCRIPTION_MAX_LEN,
            field_name=key,
        )
        for key in _STRUCTURED_FIELDS
    }
    description, done = _fold_structured_sections(description, done, additive)
    return AgentPack(
        schema=SCHEMA_ID,
        kind=kind,
        specialty=specialty,
        description=description,
        what_done_looks_like=done,
        personality_hint=personality_hint,
        tools_hint=tools_hint,
        pack_author=pack_author,
        ignored_keys=tuple(ignored),
    )


def _parse_kind(value: Any) -> str:
    """v1 is agent packs only. ``kind`` is reserved so later types need no rewrite."""
    if value is None:
        return PACK_KIND_AGENT
    if not isinstance(value, str):
        raise AgentPackError("Pack field 'kind' must be a string.", code="invalid_schema")
    kind = value.strip().lower()
    if not kind or kind == PACK_KIND_AGENT:
        return PACK_KIND_AGENT
    if kind in RESERVED_PACK_KINDS:
        raise AgentPackError(
            f"Pack kind {kind!r} is reserved. v1 imports agent packs only.",
            code="unsupported_kind",
        )
    raise AgentPackError(
        f"Pack kind must be {PACK_KIND_AGENT!r}.",
        code="invalid_schema",
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


def _optional_pack_author(value: Any) -> tuple[PackAuthor | None, list[str]]:
    if value is None:
        return None, []
    if not isinstance(value, dict):
        raise AgentPackError(
            "Pack field 'pack_author' must be a mapping with name "
            "(and optional url).",
            code="invalid_schema",
        )
    ignored = [
        f"pack_author.{raw_key}"
        for raw_key in value
        if isinstance(raw_key, str) and raw_key not in {"name", "url"}
    ]
    name = _optional_text(
        value.get("name"),
        max_len=PACK_AUTHOR_NAME_MAX_LEN,
        field_name="pack_author.name",
    )
    if not name:
        raise AgentPackError(
            "Pack field 'pack_author.name' is required when pack_author is set.",
            code="invalid_schema",
        )
    raw_url = value.get("url")
    url: str | None = None
    if raw_url is not None and not (isinstance(raw_url, str) and not raw_url.strip()):
        url = _parse_author_url(raw_url)
    return PackAuthor(name=name, url=url), ignored


def _parse_author_url(value: Any) -> str:
    if not isinstance(value, str):
        raise AgentPackError(
            "Pack field 'pack_author.url' must be a string.",
            code="invalid_schema",
        )
    url = value.strip()
    if not url:
        raise AgentPackError(
            "Pack field 'pack_author.url' must be a string.",
            code="invalid_schema",
        )
    if len(url) > PACK_AUTHOR_URL_MAX_LEN:
        raise AgentPackError(
            "Pack field 'pack_author.url' is too long.",
            code="invalid_schema",
        )
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AgentPackError(
            "Pack field 'pack_author.url' must be an http(s) URL.",
            code="invalid_schema",
        )
    if parsed.username or parsed.password:
        raise AgentPackError(
            "Pack field 'pack_author.url' must not include userinfo.",
            code="invalid_schema",
        )
    return url


def _fold_structured_sections(
    description: str,
    done: str,
    additive: dict[str, str | None],
) -> tuple[str, str]:
    """Fold additive senior fields into hire description / done strings."""
    desc_preamble, desc_sections = extract_labeled_sections(description)
    done_preamble, done_sections = extract_labeled_sections(done)
    for key in DESCRIPTION_SECTION_KEYS:
        value = additive.get(key)
        if value:
            desc_sections[key] = value
    fail = additive.get("fail_examples")
    if fail:
        done_sections["fail_examples"] = fail

    if any(desc_sections.get(key) for key in DESCRIPTION_SECTION_KEYS):
        composed = compose_labeled_description(desc_preamble, desc_sections)
        if len(composed) > HIRE_DESCRIPTION_MAX_LEN:
            raise AgentPackError(
                "Pack description with structured sections exceeds the hire-field length cap.",
                code="invalid_schema",
            )
        description = composed

    fail_body = (done_sections.get("fail_examples") or "").strip()
    if fail_body:
        success = (done_preamble or done_sections.get("done") or "").strip()
        composed_done = compose_labeled_done(success, fail_body)
        if len(composed_done) > HIRE_DONE_FAIL_BAR_MAX_LEN:
            raise AgentPackError(
                "Pack what_done_looks_like with fail examples exceeds the hire-field length cap.",
                code="invalid_schema",
            )
        done = composed_done
    return description, done


def _optional_tools_hint(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise AgentPackError(
            "Pack field 'tools_hint' must be a list of short tool names, not prose.",
            code="invalid_schema",
        )
    if isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise AgentPackError(
                    "Pack field 'tools_hint' must be a list of short tool names.",
                    code="invalid_schema",
                )
            stripped = item.strip()
            if stripped:
                items.append(stripped)
    else:
        raise AgentPackError(
            "Pack field 'tools_hint' must be a list of short tool names, not prose.",
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
        if (
            any(ch.isspace() for ch in item)
            or not item.replace("_", "").isalnum()
            or not item[0].isalpha()
        ):
            raise AgentPackError(
                "Pack field 'tools_hint' must be a list of short tool names, not prose.",
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
