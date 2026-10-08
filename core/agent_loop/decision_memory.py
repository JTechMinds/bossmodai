"""Save the ``remember`` sentence of a final conversation decision.

A decision turn adds a memory through the envelope's optional ``remember``
field instead of a separate ``memory add`` CLI round. The turn loop calls
:func:`save_decision_memory` after the decision validates and before
``apply_decision`` posts anything, so a rejected sentence can still be
repaired in the same turn. The ``memory`` command stays the tool for
replace / remove / list and for execution turns.
"""

from __future__ import annotations

from core.agent_loop import standing_prefs
from core.agent_loop.standing_prefs import Memory
from core.models import Agent


def save_decision_memory(agent: Agent, text: str) -> Memory:
    """Add one memory for ``agent`` from a decision's ``remember`` field.

    The save is one atomic write of one sentence: it either lands whole or
    not at all, so a repaired decision never stores a duplicate.

    Args:
        agent: The agent whose memory store receives the sentence.
        text: The ``remember`` sentence, already stripped and non-empty.

    Returns:
        The memory as stored, carrying its system-assigned id.

    Raises:
        standing_prefs.MemoryStoreUnreadableError: The store on disk is
            corrupt or not a file. A ``ValueError`` subclass, but a defect the
            model cannot fix by changing the text; the store is unchanged.
        ValueError: The store's one-sentence rule refused the text (empty, a
            line break, over the length limit, over the store cap). The
            message is one sentence the model can be shown; the store is
            unchanged.
        config.ConfigError: A memory limit setting is missing or invalid. A
            real defect, not a model mistake: it propagates and fails the
            turn.
        OSError: The lock or the atomic write failed. Propagates and fails
            the turn.
    """
    return standing_prefs.add_memory(agent.storage_key, text)


def memory_repair_error(reason: str) -> str:
    """Return the repair steer shown when a ``remember`` sentence was refused.

    Args:
        reason: The store's one-sentence ``ValueError`` message.

    Returns:
        The parsed-error text for the decision repair prompt.
    """
    return f'memory not saved: {reason} Fix "remember" (shorter, one line) or leave it out, then return your decision again.'
