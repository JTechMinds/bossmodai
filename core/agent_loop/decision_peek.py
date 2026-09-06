"""Decision-turn CLI peek budget: total-peek cap, fingerprints, identical-loop stop.

The soft cap is 10 CLI peeks on the decision path. Fingerprints collapse
path tweaks and detect a triple repeat; they do not recycle quota.
``request_host_access`` is not a peek. Deep host review is accept → execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.bm_cli.parser import parse_cli_command

SOFT_PEEK_BUDGET = 10
IDENTICAL_PEEK_STREAK_LIMIT = 3

SOFT_PEEK_STEER = "peek budget exhausted — decide or accept work"
IDENTICAL_PEEK_STEER = "looping — decide or accept work"


@dataclass(frozen=True, slots=True)
class PeekBudgetVerdict:
    """Whether one more CLI peek may run on the decision path."""

    allowed: bool
    reason: str | None = None
    steer: str = ""


@dataclass
class DecisionPeekBudget:
    """Track total CLI peeks and identical-in-a-row streaks.

    Fingerprints collapse path tweaks and detect a triple repeat. They do
    not recycle quota: every allowed ``bm_cli`` peek spends one of the 10.
    """

    peek_count: int = 0
    last_fingerprint: str | None = None
    identical_streak: int = 0

    def consider(self, command: str, content: str | None = None) -> PeekBudgetVerdict:
        """Record one CLI peek and return whether it may execute."""
        fingerprint = normalize_peek_fingerprint(command, content)
        next_streak = self.identical_streak + 1 if fingerprint == self.last_fingerprint else 1

        if next_streak >= IDENTICAL_PEEK_STREAK_LIMIT:
            return PeekBudgetVerdict(
                allowed=False,
                reason="identical_loop",
                steer=IDENTICAL_PEEK_STEER,
            )
        if self.peek_count >= SOFT_PEEK_BUDGET:
            return PeekBudgetVerdict(
                allowed=False,
                reason="soft_budget",
                steer=SOFT_PEEK_STEER,
            )

        self.last_fingerprint = fingerprint
        self.identical_streak = next_streak
        self.peek_count += 1
        return PeekBudgetVerdict(allowed=True)


def normalize_peek_fingerprint(command: str, content: str | None = None) -> str:
    """Stable identity for a decision-turn CLI peek.

    Path tweaks must not dodge the budget: ``ls a`` ≡ ``ls a/`` ≡ ``ls ./a``.
    Command aliases and extra whitespace collapse. Write-body content is part
    of the identity when present. ``request_host_access`` is not a peek.
    """
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        collapsed = " ".join((command or "").split())
        return _join_fingerprint(collapsed.lower(), content)

    args = tuple(
        normalized
        for normalized in (_normalize_peek_arg(arg) for arg in parsed.args)
        if normalized
    )
    body = " ".join((parsed.name, *args)).strip()
    return _join_fingerprint(body, content)


def _join_fingerprint(command_body: str, content: str | None) -> str:
    extra = (content or "").strip()
    if extra:
        return f"{command_body}\n{extra}"
    return command_body


def _normalize_peek_arg(arg: str) -> str:
    token = arg.strip()
    if token.startswith("-") and token != "-":
        return token
    return _normalize_peek_path(token)


def _normalize_peek_path(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    if not text:
        return ""
    while "//" in text:
        text = text.replace("//", "/")
    if text != "/":
        text = text.rstrip("/")
    while text.startswith("./"):
        text = text[2:]
        if text != "/":
            text = text.rstrip("/")
    if text in {"", "."}:
        return ""

    absolute = text.startswith("/")
    parts: list[str] = []
    for item in text.split("/"):
        if item in {"", "."}:
            continue
        if item == "..":
            if parts:
                parts.pop()
            continue
        parts.append(item)
    if absolute:
        return "/" + "/".join(parts) if parts else "/"
    return "/".join(parts)
