"""BossMod AI — validate and present a manifest settings form (pure, no I/O).

One implementation of field validation and secret masking for both stores of
declared values: an agent's ``agent_config`` and the extension's own
``settings``. Callers own transport (HTTP errors) and storage; these
functions take a :class:`~core.extensions.manifest.ConfigSpec` and a stored
dict and return plain data.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from core.extensions.manifest import ConfigField, ConfigSpec, config_value

# An address shape check, not RFC validation: the service is the authority.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigValueError(ValueError):
    """A submitted value breaks the declared form; the message is shown to the operator."""


def resolve_values(
    spec: ConfigSpec,
    submitted: Mapping[str, str],
    stored: Mapping[str, str] | None,
) -> dict[str, str]:
    """Validate a submitted config and fill blank secrets from the stored one.

    A blank secret while a config is stored means "keep the stored secret"
    (the dialog says so); with nothing stored it is a missing value. A kept
    secret is not checked against ``min_length`` again: it passed when it
    was saved.

    Args:
        spec: The declared form.
        submitted: What the operator sent, by field key.
        stored: The values stored now, or ``None`` when nothing is stored.

    Returns:
        Every declared field's trimmed, validated value.

    Raises:
        ConfigValueError: An unknown key, a missing required value, a value
            shorter than its ``min_length``, a bad email, or a number outside
            its bounds (the first problem).
    """
    declared = {item.key for item in spec.fields}
    unknown = sorted(set(submitted) - declared)
    if unknown:
        raise ConfigValueError(f"Unknown field: {', '.join(unknown)}")
    resolved: dict[str, str] = {}
    for item in spec.fields:
        value = submitted.get(item.key, "").strip()
        kept = item.kind == "secret" and not value and stored is not None and bool(stored.get(item.key))
        if kept:
            value = stored[item.key]
        if item.required and not value:
            raise ConfigValueError(f"{item.label} is required.")
        if item.min_length is not None and value and not kept and len(value) < item.min_length:
            raise ConfigValueError(f"{item.label} must be at least {item.min_length} characters.")
        if item.kind == "email" and value and not _EMAIL_RE.match(value):
            raise ConfigValueError(f"{item.label} is not an email address.")
        if item.kind == "number":
            value = number_value(item, value)
        resolved[item.key] = value
    return resolved


def number_value(item: ConfigField, value: str) -> str:
    """Return a number field's value to store: blank is its default, else a whole number in bounds.

    Raises:
        ConfigValueError: Not a whole number, or outside ``min``/``max``
            (the message names the allowed range).
    """
    if not value:
        return item.default
    try:
        number = int(value)
    except ValueError:
        number = None
    if number is None or (item.min is not None and number < item.min) or (item.max is not None and number > item.max):
        raise ConfigValueError(f"{item.label} must be a whole number{range_text(item)}.")
    return str(number)


def range_text(item: ConfigField) -> str:
    """Return the phrase naming a number field's bounds, or an empty string when it has none."""
    if item.min is not None and item.max is not None:
        return f" from {item.min} to {item.max}"
    if item.min is not None:
        return f" of at least {item.min}"
    if item.max is not None:
        return f" of at most {item.max}"
    return ""


def unset_fields(spec: ConfigSpec, stored: Mapping[str, str] | None) -> tuple[str, ...]:
    """Return the labels of declared fields a stored config cannot answer for.

    A field is unset when its key is absent from ``stored`` and it has no
    manifest ``default``: exactly the fields
    :func:`core.extensions.manifest.config_value` would refuse. That happens
    when nothing was ever saved, or a save predates the field.

    Args:
        spec: The declared form.
        stored: The values stored now; ``None`` (nothing stored) counts as
            an empty mapping.

    Returns:
        The unset fields' labels, in declared order; empty when every field
        has a value or a default.
    """
    present: Mapping[str, str] = stored if stored is not None else {}
    return tuple(item.label for item in spec.fields if item.key not in present and item.default is None)


def masked_fields(spec: ConfigSpec, stored: Mapping[str, str] | None) -> list[dict[str, Any]]:
    """Return the form's fields as the UI sees them: a secret only says whether it is set.

    Args:
        spec: The declared form.
        stored: The values stored now, or ``None`` when nothing is stored.

    Returns:
        One dict per field, in declared order: ``key``, ``label``, ``kind``,
        ``required``, ``hint`` (a sentence, or ``None``) and, by kind,
        ``set`` (secret) or ``value`` (the stored value; a number field with
        nothing stored, or stored before the field existed, reads as its
        declared default).
    """
    fields = []
    for item in spec.fields:
        field: dict[str, Any] = {
            "key": item.key, "label": item.label, "kind": item.kind, "required": item.required, "hint": item.hint,
        }
        if item.kind == "secret":
            field["set"] = bool(stored and stored.get(item.key))
        elif item.kind == "number":
            field.update({"min": item.min, "max": item.max, "default": item.default})
            field["value"] = config_value(spec, stored, item.key) if stored else item.default
        else:
            field["value"] = stored.get(item.key, "") if stored else ""
        fields.append(field)
    return fields
