"""Decision-turn CLI peek budget: fingerprints, soft cap, identical-loop stop.

Decision may look around a little. Deep multi-step host review is accept →
execution, not an unbounded decision digathon.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    """Track distinct normalized peeks and identical-in-a-row streaks."""

    seen: set[str] = field(default_factory=set)
    last_fingerprint: str | None = None
    identical_streak: int = 0

    def consider(self, command: str, content: str | None = None) -> PeekBudgetVerdict:
        """Record one CLI peek and return whether it may execute."""
        fingerprint = normalize_peek_fingerprint(command, content)
        if fingerprint == self.last_fingerprint:
            self.identical_streak += 1
        else:
            self.last_fingerprint = fingerprint
            self.identical_streak = 1

        if self.identical_streak >= IDENTICAL_PEEK_STREAK_LIMIT:
            return PeekBudgetVerdict(
                allowed=False,
                reason="identical_loop",
                steer=IDENTICAL_PEEK_STEER,
            )
        if fingerprint not in self.seen and len(self.seen) >= SOFT_PEEK_BUDGET:
            return PeekBudgetVerdict(
                allowed=False,
                reason="soft_budget",
                steer=SOFT_PEEK_STEER,
            )
        self.seen.add(fingerprint)
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
