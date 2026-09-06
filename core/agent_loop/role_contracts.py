"""Role-contract v1 helpers: specialty matching and checkable done claims.

Hire stores a one-line specialty on ``Agent.role``, an optional casual
description, and a short done/fail bar (finish line). Assign/routing and
complete/deliver use those fields on the existing task paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from core.agent_loop.deliverables import get_work_contract, missing_deliverables, summarize_deliverable
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent
from core.models.task import AssigneeSuggestion, Task

SpecialtyFamily = Literal["write", "review", "implement", "research", "design", "coordinate"]
MatchStatus = Literal["match", "unknown", "mismatch"]
DoneClaimType = Literal["artifact", "tests", "proof"]

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_SPECIALTY_TOKENS: dict[SpecialtyFamily, frozenset[str]] = {
    "write": frozenset({
        "writer", "writing", "copy", "copywriter", "docs", "documentation",
        "documenter", "author", "editor", "draft",
    }),
    "review": frozenset({
        "reviewer", "review", "auditor", "audit", "qa", "tester", "test",
        "testing", "inspector", "inspect",
    }),
    "implement": frozenset({
        "engineer", "eng", "developer", "dev", "coder", "programmer",
        "implement", "implementation", "code", "coding", "builder",
    }),
    "research": frozenset({
        "researcher", "research", "analyst", "analysis", "investigate",
    }),
    "design": frozenset({
        "designer", "design", "ux", "ui", "mockup",
    }),
    "coordinate": frozenset({
        "pm", "product", "manager", "lead", "coordinator", "owner", "director",
    }),
}

_WORK_KIND_TOKENS: dict[SpecialtyFamily, frozenset[str]] = {
    "write": frozenset({"write", "writing", "draft", "document", "docs", "copy", "author", "edit", "edits"}),
    "review": frozenset({"review", "audit", "auditing", "qa", "test", "tests", "testing", "inspect"}),
    "implement": frozenset({"implement", "code", "coding", "build", "fix", "debug", "develop"}),
    "research": frozenset({"research", "analyze", "analysis", "investigate"}),
    "design": frozenset({"design", "mockup", "wireframe", "ux"}),
}

_FAMILY_LABELS: dict[SpecialtyFamily, str] = {
    "write": "writing",
    "review": "review/audit",
    "implement": "implementation",
    "research": "research",
    "design": "design",
    "coordinate": "coordination",
}

_DONE_CLAIM_TYPES = frozenset({"artifact", "tests", "proof"})

# Operator-facing finish-line defaults. Empty done stays blocked by the
# checkable-claim rules even when the stored bar is blank.
_DEFAULT_FINISH_LINES: dict[SpecialtyFamily, str] = {
    "write": "A named draft or document exists. Empty done does not count.",
    "review": "A checkable allow/deny (or tests/artifact) exists. Empty done does not count.",
    "implement": "Tests evidence or a named artifact exists. Empty done does not count.",
    "research": "A named findings note exists. Empty done does not count.",
    "design": "A named mockup or design file exists. Empty done does not count.",
    "coordinate": "A named plan or status note exists. Empty done does not count.",
}
_FALLBACK_FINISH_LINE = (
    "A checkable claim exists (tests, artifact, or allow/deny). Empty done does not count."
)

# Soft-deny only when work is clearly outside the hire specialty.
# Engineers writing a report is unknown, not a mismatch.
_CLEAR_CONFLICTS = frozenset({
    frozenset({"write", "review"}),
    frozenset({"design", "review"}),
    frozenset({"design", "implement"}),
})


@dataclass(frozen=True)
class SpecialtyAssignment:
    """Result of comparing one assignee specialty against inferred work kind."""

    status: MatchStatus
    work_kind: SpecialtyFamily | None
    assignee_family: SpecialtyFamily | None
    warning: str | None
    suggested: list[Agent] = field(default_factory=list)
    deny: bool = False


@dataclass(frozen=True)
class DoneClaim:
    """A checkable completion claim attached to a done/deliver action."""

    type: DoneClaimType
    path: str | None = None
    evidence: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {"type": self.type}
        if self.path:
            payload["path"] = self.path
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


def tokenize(text: str | None) -> set[str]:
    """Lowercase alphanumeric tokens from free text."""
    if not text:
        return set()
    return set(_TOKEN_RE.findall(text.lower()))


def specialty_family(role: str | None) -> SpecialtyFamily | None:
    """Map a hire specialty (``Agent.role``) onto one v1 family, if clear."""
    tokens = tokenize(role)
    if not tokens:
        return None
    hits = [
        family
        for family, family_tokens in _SPECIALTY_TOKENS.items()
        if tokens & family_tokens
    ]
    specific = [family for family in hits if family != "coordinate"]
    if len(specific) == 1:
        return specific[0]
    if len(specific) > 1:
        return None
    if hits == ["coordinate"]:
        return "coordinate"
    return None


def infer_work_kind(
    title: str | None,
    description: str | None = None,
    *,
    requested_specialty: str | None = None,
) -> SpecialtyFamily | None:
    """Infer work kind from an explicit requested specialty or title/description."""
    requested = specialty_family(requested_specialty)
    if requested and requested != "coordinate":
        return requested
    tokens = tokenize(title) | tokenize(description)
    if not tokens:
        return requested
    scores: dict[SpecialtyFamily, int] = {}
    for family, family_tokens in _WORK_KIND_TOKENS.items():
        overlap = tokens & family_tokens
        if overlap:
            scores[family] = len(overlap)
    if not scores:
        return requested
    best = max(scores.values())
    winners = [family for family, score in scores.items() if score == best]
    if len(winners) == 1:
        return winners[0]
    return None


def is_auditor_specialty(role: str | None) -> bool:
    """Return whether this hire specialty is auditor-style (CLEAR subset)."""
    return specialty_family(role) == "review"


def match_specialty(
    *,
    assignee_role: str | None,
    work_kind: SpecialtyFamily | None,
) -> MatchStatus:
    """Compare one assignee specialty against an inferred work kind."""
    if work_kind is None:
        return "unknown"
    family = specialty_family(assignee_role)
    if family is None or family == "coordinate":
        return "unknown"
    if family == work_kind:
        return "match"
    if frozenset({family, work_kind}) in _CLEAR_CONFLICTS:
        return "mismatch"
    return "unknown"


def rank_agents_for_work(
    agents: list[Agent],
    *,
    title: str | None,
    description: str | None = None,
    requested_specialty: str | None = None,
) -> list[Agent]:
    """Prefer teammates whose specialty matches the inferred work kind."""
    work_kind = infer_work_kind(title, description, requested_specialty=requested_specialty)
    order = {"match": 0, "unknown": 1, "mismatch": 2}
    return sorted(
        agents,
        key=lambda agent: (
            order[match_specialty(assignee_role=agent.role, work_kind=work_kind)],
            (agent.name or "").lower(),
        ),
    )


def prefer_specialty_match(
    candidates: list[Agent],
    *,
    title: str | None,
    description: str | None = None,
    requested_specialty: str | None = None,
) -> Agent | None:
    """If exactly one candidate matches the work kind, return that agent."""
    work_kind = infer_work_kind(title, description, requested_specialty=requested_specialty)
    if work_kind is None:
        return None
    matched = [
        agent
        for agent in candidates
        if match_specialty(assignee_role=agent.role, work_kind=work_kind) == "match"
    ]
    if len(matched) == 1:
        return matched[0]
    return None


def evaluate_specialty_assignment(
    *,
    assignee: Agent,
    title: str | None,
    description: str | None = None,
    requested_specialty: str | None = None,
    teammates: list[Agent] | None = None,
    confirm: bool = False,
) -> SpecialtyAssignment:
    """Warn / soft-deny when assigned work is clearly outside the hire specialty."""
    work_kind = infer_work_kind(title, description, requested_specialty=requested_specialty)
    assignee_family = specialty_family(assignee.role)
    status = match_specialty(assignee_role=assignee.role, work_kind=work_kind)
    suggested: list[Agent] = []
    if work_kind is not None and teammates:
        suggested = [
            agent
            for agent in rank_agents_for_work(
                [item for item in teammates if item.id != assignee.id],
                title=title,
                description=description,
                requested_specialty=requested_specialty,
            )
            if match_specialty(assignee_role=agent.role, work_kind=work_kind) == "match"
        ]
    if status != "mismatch":
        return SpecialtyAssignment(
            status=status,
            work_kind=work_kind,
            assignee_family=assignee_family,
            warning=None,
            suggested=suggested,
            deny=False,
        )

    work_label = _FAMILY_LABELS.get(work_kind, work_kind or "this work")
    assignee_label = assignee.role or "unspecified specialty"
    suggestion = ""
    if suggested:
        names = ", ".join(agent.name for agent in suggested[:3])
        suggestion = f" Prefer {names}."
    else:
        suggestion = " Pick a teammate whose specialty matches, or confirm the mismatch."
    warning = (
        f'{assignee.name} is "{assignee_label}"; this work looks like {work_label}.'
        f"{suggestion}"
    )
    return SpecialtyAssignment(
        status="mismatch",
        work_kind=work_kind,
        assignee_family=assignee_family,
        warning=warning,
        suggested=suggested,
        deny=not confirm,
    )


def suggested_assignees(agents: list[Agent]) -> list[AssigneeSuggestion]:
    """Serialize ranked teammate suggestions for assign API/UI."""
    return [
        AssigneeSuggestion(id=agent.id, name=agent.name, role=agent.role, match="match")
        for agent in agents
    ]


def suggest_finish_line(
    specialty: str | None,
    description: str | None = None,
) -> str:
    """Suggest a default finish line from specialty, using description when useful.

    Does not persist or rewrite stored text. Blank ``done_fail_bar`` stays valid;
    empty done is still rejected by the checkable-claim rules.
    """
    family = specialty_family(specialty)
    if family is None:
        family = infer_work_kind(None, description)
    if family is None:
        return _FALLBACK_FINISH_LINE
    return _DEFAULT_FINISH_LINES[family]


def format_role_contract_block(agent: Agent) -> str:
    """Render the hire contract the model must follow on every turn."""
    specialty = (agent.role or "").strip() or "unspecified"
    description = (getattr(agent, "description", None) or "").strip()
    bar = (agent.done_fail_bar or "").strip() or (
        "Good: a checkable claim exists. Fail: empty done with no evidence."
    )
    auditor = is_auditor_specialty(agent.role)
    clear_line = (
        "Auditor CLEAR only against a checkable claim; empty done is not a CLEAR."
        if auditor
        else "Empty done is rejected."
    )
    description_line = f"Description: {description}\n" if description else ""
    return (
        "# Role contract\n"
        f"Specialty: {specialty}\n"
        f"{description_line}"
        f"Done/fail bar: {bar}\n"
        "- Prefer teammates whose specialty matches the work. "
        "Do not assign review/audit work to a writer, or writing to an auditor, "
        "unless the mismatch was confirmed.\n"
        "- Complete/deliver requires a checkable claim: a satisfied file deliverable, "
        "or data.claim {type: artifact|tests|proof, path?, ev?}. "
        f"{clear_line}"
    )


def operator_done_claim_guidance(
    *,
    auditor: bool = False,
    done_fail_bar: str | None = None,
    has_file_deliverables: bool = False,
) -> str:
    """Operator-facing copy for what a checkable done claim looks like."""
    if has_file_deliverables:
        base = (
            "Complete requires the work-contract file path to exist "
            "(that file is the checkable claim)."
        )
    elif auditor:
        base = (
            "Auditor CLEAR requires a checkable claim: tests evidence, "
            "an artifact path that exists, or an allow/deny proof summary."
        )
    else:
        base = (
            "Complete/deliver requires a checkable claim: tests evidence, "
            "an artifact path that exists, or an allow/deny proof summary. "
            "Empty done is rejected."
        )
    bar = (done_fail_bar or "").strip()
    if bar:
        return f"{base} What done looks like for this agent: {bar}"
    return base


def parse_done_claim_from_text(content: str | None) -> dict[str, str] | None:
    """Parse a structured claim from a completion event or summary line."""
    if not content:
        return None
    marker = " Claim: "
    if marker not in content:
        return None
    tail = content.split(marker, 1)[1].strip()
    if not tail:
        return None
    parts = [part.strip() for part in tail.split("—")]
    claim_type = parts[0].lower() if parts else ""
    if claim_type not in _DONE_CLAIM_TYPES:
        return {"type": "proof", "evidence": tail}
    payload: dict[str, str] = {"type": claim_type}
    if len(parts) > 1 and parts[1]:
        if claim_type == "artifact" or parts[1].startswith("/"):
            payload["path"] = parts[1]
        else:
            payload["evidence"] = parts[1]
    if len(parts) > 2 and parts[2]:
        payload["evidence"] = parts[2]
    return payload


def empty_done_claim_message(*, auditor: bool) -> str:
    """Operator/model-facing rejection for complete/deliver with no checkable claim."""
    if auditor:
        return (
            "Auditor CLEAR requires a checkable claim "
            "(tests evidence, artifact path, or allow/deny proof). "
            "Empty done is not a CLEAR."
        )
    return (
        "Marking work complete requires a checkable claim: a satisfied file deliverable, "
        'or data.claim {type: artifact|tests|proof, path?, ev?}.'
    )


def resolve_done_claim(
    *,
    agent: Agent,
    task: Task | None,
    action: dict[str, Any],
) -> tuple[DoneClaim | None, dict[str, Any] | None]:
    """Return a checkable done claim, or a world_feedback payload to reject empty done.

    Satisfied work-contract file deliverables count as an artifact claim. Otherwise
    the complete action must attach ``doneClaim`` / ``claim``. Auditor-style
    specialties use the same complete path; v1 CLEAR is this check, not a
    separate protocol.
    """
    pending = missing_deliverables(
        agent_id=agent.id,
        agent_storage_key=agent.storage_key,
        task=task,
    )
    if pending:
        first = summarize_deliverable(pending[0])
        return None, {
            "event": "world_feedback",
            "detail": (
                f"Required deliverable missing: {first}. "
                "Satisfy all declared deliverables before complete."
            ),
            "agent_name": agent.name,
            "missing_deliverables": [item.model_dump() for item in pending],
        }

    contract = get_work_contract(task)
    if contract.deliverables:
        path = summarize_deliverable(contract.deliverables[0])
        return DoneClaim(type="artifact", path=path), None

    raw = action.get("doneClaim")
    if raw in (None, ""):
        raw = action.get("claim")
    parsed, error = _parse_done_claim(raw)
    if error:
        return None, {
            "event": "world_feedback",
            "detail": error,
            "agent_name": agent.name,
        }
    if parsed is None:
        return None, {
            "event": "world_feedback",
            "detail": empty_done_claim_message(auditor=is_auditor_specialty(agent.role)),
            "agent_name": agent.name,
        }

    if parsed.type == "artifact":
        if not parsed.path:
            return None, {
                "event": "world_feedback",
                "detail": 'Artifact claims require a non-empty "path".',
                "agent_name": agent.name,
            }
        resolved = resolve_cli_path(agent.storage_key, "/", parsed.path)
        if not resolved.exists or resolved.real_path is None or not resolved.real_path.is_file():
            return None, {
                "event": "world_feedback",
                "detail": (
                    f'Artifact claim path "{parsed.path}" does not exist as a file. '
                    "Write the deliverable before complete."
                ),
                "agent_name": agent.name,
            }
        return DoneClaim(type="artifact", path=resolved.virtual_path, evidence=parsed.evidence), None

    if not parsed.evidence:
        kind = "Tests" if parsed.type == "tests" else "Proof"
        return None, {
            "event": "world_feedback",
            "detail": f"{kind} claims require non-empty evidence (data.claim.ev).",
            "agent_name": agent.name,
        }
    return parsed, None


def _parse_done_claim(raw: Any) -> tuple[DoneClaim | None, str | None]:
    """Normalize a compact or canonical done-claim payload."""
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, dict):
        return None, 'done claim must be an object {type, path?, ev?}'
    extra = set(raw) - {"type", "path", "ev", "evidence"}
    if extra:
        return None, f'unexpected done-claim keys: {", ".join(sorted(extra))}'
    claim_type = raw.get("type")
    if not isinstance(claim_type, str) or claim_type.strip() not in _DONE_CLAIM_TYPES:
        return None, 'done claim "type" must be artifact, tests, or proof'
    path = raw.get("path")
    if path in (None, ""):
        path_value = None
    elif isinstance(path, str) and path.strip():
        path_value = path.strip()
    else:
        return None, 'done claim "path" must be a non-empty string when provided'
    evidence = raw.get("evidence")
    if evidence in (None, ""):
        evidence = raw.get("ev")
    if evidence in (None, ""):
        evidence_value = None
    elif isinstance(evidence, str) and evidence.strip():
        evidence_value = evidence.strip()
    else:
        return None, 'done claim evidence must be a non-empty string when provided'
    normalized_type: DoneClaimType = claim_type.strip()  # type: ignore[assignment]
    return DoneClaim(type=normalized_type, path=path_value, evidence=evidence_value), None
