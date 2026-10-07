"""BossMod AI — The human user's one identity: "the boss".

Every agent-facing reference to the human (speaker labels, thread history,
mentions, task events) comes from here. Callers ask "who is the boss"; they
never spell the name. The UI shows "You" on the human's own lines and does
not use this label.

The name lives in the ``boss_name`` settings row (empty means unset) and is
read through the process config cache, which each process refreshes at the
start of every request or turn, so a rename is live on the next turn.
"""

from __future__ import annotations

from collections.abc import Iterable

BOSS_ROLE_NAME = "Boss"
# Matches the codebase's other name caps (PACK_AUTHOR_NAME_MAX_LEN).
BOSS_NAME_MAX_LENGTH = 80
# "boss" always means the human; "everyone"/"all" are thread-wide mentions.
RESERVED_BOSS_NAMES = frozenset({"boss", "everyone", "all"})


def boss_name() -> str | None:
    """Return the name the boss entered, or ``None`` when it is unset.

    Returns:
        The trimmed ``boss_name`` setting, or ``None`` when it is empty.

    Raises:
        core.config.ConfigError: The ``boss_name`` settings row is missing.
    """
    # Imported here, not at module load: core.config imports db, whose import
    # chain reaches modules that import this one (next_owner, runtime_core).
    from core import config

    value = config.require_present("boss_name")
    return value or None


def boss_label() -> str:
    """Return how agents see the human: ``Boss`` or ``<Name> (the boss)``.

    Used as the speaker on the human's lines in every model-facing
    transcript, and as the audit snapshot written into ``author_name``
    columns (which are never shown again; readers resolve by author type).

    Raises:
        core.config.ConfigError: The ``boss_name`` settings row is missing.
    """
    name = boss_name()
    if name is None:
        return BOSS_ROLE_NAME
    return f"{name} (the boss)"


def boss_mention() -> str:
    """Return the mention that tags the human: ``@<Name>``, or ``@Boss`` when unset.

    Raises:
        core.config.ConfigError: The ``boss_name`` settings row is missing.
    """
    return "@" + (boss_name() or BOSS_ROLE_NAME)


def boss_mention_names() -> tuple[str, ...]:
    """Return every name a mention may use to tag the human.

    ``Boss`` is always valid, so static prompt text can say ``@Boss``; the
    entered name is added when one is set. Read per call, never cached at
    import, so a rename takes effect on the next turn.

    Returns:
        ``("Boss",)`` or ``("Boss", <name>)``.

    Raises:
        core.config.ConfigError: The ``boss_name`` settings row is missing.
    """
    name = boss_name()
    if name is None:
        return (BOSS_ROLE_NAME,)
    return (BOSS_ROLE_NAME, name)


def validate_boss_name(value: str, *, agent_names: Iterable[str]) -> str:
    """Validate a boss name at the write boundary and return it trimmed.

    An empty value is valid and means "unset" (agents say ``Boss``). The
    checks keep mention parsing unambiguous: the name can never collide with
    ``@Boss``, the thread-wide mentions, or an existing agent.

    Args:
        value: The raw name as submitted.
        agent_names: Names of every existing agent.

    Returns:
        The trimmed name (possibly ``""``).

    Raises:
        ValueError: With a user-readable message when the name is too long,
            holds a newline/control character or ``@``, is reserved, or
            equals an existing agent's name (case-insensitive).
    """
    name = value.strip()
    if not name:
        return ""
    if len(name) > BOSS_NAME_MAX_LENGTH:
        raise ValueError(f"Your name can be at most {BOSS_NAME_MAX_LENGTH} characters.")
    if any(ch in "\r\n" or not ch.isprintable() for ch in name):
        raise ValueError("Your name must be on one line, without control characters.")
    if "@" in name:
        raise ValueError('Your name cannot contain "@".')
    folded = name.casefold()
    if folded in RESERVED_BOSS_NAMES:
        raise ValueError(f'"{name}" is reserved. Pick another name.')
    if any(folded == (agent or "").strip().casefold() for agent in agent_names):
        raise ValueError(f'An agent is already named "{name}". Pick another name.')
    return name


def ensure_agent_name_allowed(name: str) -> None:
    """Refuse an agent name that would make a mention of the human ambiguous.

    Called on hire and rename. ``Boss`` (any case) always means the human,
    and so does the boss's own name while one is set.

    Args:
        name: The proposed agent name.

    Raises:
        ValueError: The name equals ``boss`` or the current boss name
            (case-insensitive).
        core.config.ConfigError: The ``boss_name`` settings row is missing.
    """
    folded = name.strip().casefold()
    current = boss_name()
    if folded == BOSS_ROLE_NAME.casefold() or (current is not None and folded == current.casefold()):
        raise ValueError(f'"{name}" is reserved for you (the boss). Pick another agent name.')
