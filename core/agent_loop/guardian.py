"""BossMod AI — Guardian: pathological behavior detection.

Runs after every agent action at zero API cost (pure local logic).
Four rules from the vision doc:
  1. Token explosion — single output exceeds per-agent token limit
  2. Velocity burst — too many messages sent per minute
  3. Repetition — consecutive near-identical outputs
  4. No-progress — too many actions without progress (for multi-turn loop)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.models import Agent
import db

logger = logging.getLogger(__name__)


class GuardianViolation:
    """Result of a failed Guardian check."""

    def __init__(self, rule: str, detail: str, hard_stop: bool = True) -> None:
        self.rule = rule
        self.detail = detail
        self.hard_stop = hard_stop  # True = immediate freeze, False = soft pause


def check_post_action(
    agent: Agent,
    action: dict[str, Any],
    response_content: str,
) -> GuardianViolation | None:
    """Run hard-stop Guardian checks after an action. Returns violation or None.

    Checks: token explosion, velocity burst, repetition.
    Called after every execute_action() in the multi-turn loop.
    """
    # 1. Token explosion — single output exceeds limit
    from core.llm.client import count_tokens

    token_count = count_tokens(response_content)
    if token_count > agent.guardian_token_limit:
        return GuardianViolation(
            "token_explosion",
            f"Output is {token_count} tokens (limit: {agent.guardian_token_limit})",
        )

    # 2. Velocity burst — too many messages sent per minute
    # Use naive UTC to match DuckDB's naive timestamps
    now = datetime.utcnow()
    one_min_ago = now - timedelta(seconds=60)
    recent = db.get_messages_for_agent(agent.id, limit=agent.guardian_velocity_limit + 5)
    recent_sent = [
        m for m in recent
        if m.from_agent == agent.id and m.created_at and m.created_at > one_min_ago
    ]
    if len(recent_sent) >= agent.guardian_velocity_limit:
        return GuardianViolation(
            "velocity_burst",
            f"{len(recent_sent)} messages in 60s (limit: {agent.guardian_velocity_limit})",
        )

    # 3. Repetition — consecutive near-identical outputs
    action_type = action.get("action", "")
    if action_type in ("work", "message"):
        content = action.get("output") or action.get("content") or ""
        if content:
            recent_own = [m for m in recent if m.from_agent == agent.id][-3:]
            consecutive_similar = sum(
                1
                for m in recent_own
                if _word_similarity(content, m.content) >= agent.guardian_repetition_threshold
            )
            if consecutive_similar >= 3:
                return GuardianViolation(
                    "repetition",
                    f"3+ consecutive outputs with >{agent.guardian_repetition_threshold:.0%} similarity",
                )

    return None


def check_no_progress(
    agent: Agent,
    action_count: int,
) -> GuardianViolation | None:
    """No-progress detection for the multi-turn loop.

    Vision doc: "agent takes more than N actions AND no new memory nodes
    in the last 10 actions AND task status has not changed → pause."

    Since memory extraction isn't implemented yet, checks action count
    against the per-agent guardian_no_progress_threshold.
    """
    if action_count < agent.guardian_no_progress_threshold:
        return None

    return GuardianViolation(
        "no_progress",
        f"{action_count} actions with no progress (limit: {agent.guardian_no_progress_threshold})",
        hard_stop=False,
    )


def _word_similarity(a: str, b: str) -> float:
    """Fast Jaccard similarity on word sets. Zero API cost."""
    if not a or not b:
        return 0.0
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    intersection = a_words & b_words
    union = a_words | b_words
    return len(intersection) / len(union)
