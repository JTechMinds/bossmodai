"""In-chat host-path consent: request, reuse, and resume onto the allowlist."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import db
from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope
from core.bm_cli.host_roots import (
    SETTING_CATEGORY,
    SETTING_KEY,
    denial_message,
    grantable_host_root,
    is_within_roots,
    looks_like_named_absolute_path,
    named_path_roots,
    normalize_host_root_setting,
)
from core.bm_cli.results import consent_required_result, error_result, success_result
from core.bm_cli.types import BossModCliResult
from core.models import Agent
from core.models.host_path_consent import HostPathConsentRequest

ConsentDecision = Literal["allow_once", "always_allow", "deny"]

_ABS_PATH_TOKEN = re.compile(r"(?<![\w])/[A-Za-z0-9._~+-]+(?:/[A-Za-z0-9._~+-]+)+")
_VERBAL_ACCESS_ASK = re.compile(
    r"(?i)(?:"
    r"please confirm|"
    r"confirm(?:\s+\w+){0,6}\s+(?:that\s+)?(?:i |you )?(?:can |may )|"
    r"(?:may|can) i (?:please )?(?:access|read|open|write|use|cat|touch)|"
    r"(?:need|request(?:ing)?|asking for) (?:your )?(?:permission|consent|access)|"
    r"allow (?:me |access|once)|"
    r"always allow|"
    r"yes\s*/\s*no|"
    r"yes or no|"
    r"do you (?:allow|approve|permit)|"
    r"permission to|"
    r"consent (?:to|for)"
    r")"
)
_HOST_PATH_TOPIC = re.compile(
    r"(?i)(?:"
    r"host[- ]path|host[- ]file|"
    r"outside (?:the |my |your )?(?:allowed )?(?:workspace )?roots?|"
    r"allowed roots"
    r")"
)
_REQUEST_HOST_ACCESS_COMMAND = "request_host_access"


class HostAccessCall(BaseModel):
    """Validated first-class host-path access request."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["request_host_access"]
    path: str
    reason: str
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "HostAccessCall":
        if not self.path.strip():
            raise ValueError('"request_host_access" requires a non-empty "path"')
        if not self.reason.strip():
            raise ValueError('"request_host_access" requires a non-empty "why"')
        return self


def maybe_parse_host_access_call(payload: Any) -> HostAccessCall | None:
    """Return a validated host-access call from the model-facing compact payload."""
    if not isinstance(payload, dict) or payload.get("act") != "request_host_access":
        return None
    extra_root = set(payload) - {"act", "data", "th"}
    if extra_root:
        raise ValueError(f'unexpected top-level keys: {", ".join(sorted(extra_root))}')
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError('"data" must be an object for act="request_host_access"')
    extra_data = set(data) - {"path", "why"}
    if extra_data:
        raise ValueError(f'unexpected request_host_access data keys: {", ".join(sorted(extra_data))}')
    return HostAccessCall.model_validate(
        {
            "action": "request_host_access",
            "path": data.get("path"),
            "reason": data.get("why"),
            "thought": payload.get("th", ""),
        }
    )


def is_verbal_host_access_ask(text: str | None) -> bool:
    """Return True when prose is asking the operator to grant host-path access."""
    blob = str(text or "").strip()
    if not blob or not _VERBAL_ACCESS_ASK.search(blob):
        return False
    if _HOST_PATH_TOPIC.search(blob):
        return True
    return any(
        looks_like_named_absolute_path(match.group(0))
        for match in _ABS_PATH_TOKEN.finditer(blob)
    )


def verbal_host_access_steer(agent: Agent) -> dict[str, Any]:
    """Fail closed when the model tries to negotiate host access in chat."""
    return {
        "event": "world_feedback",
        "detail": (
            "Host-path access is not negotiated in chat. "
            "Call request_host_access with data.path and data.why, "
            "or attempt cli on that path. "
            "The operator decides on the Allow once / Always allow / Deny card."
        ),
        "agent_name": agent.name,
        "expected_action": "request_host_access",
        "expected_actions": ["request_host_access", "cli"],
    }


def canonical_host_path(raw_path: str) -> str:
    """Return a stable absolute path string for pending/denied matching."""
    from pathlib import Path

    token = (raw_path or "").strip()
    if not token:
        return token
    path = Path(token).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def is_request_host_access_command(command: str | None) -> bool:
    """Return True when a consent request was opened without a CLI command."""
    token = (command or "").strip()
    return not token or token == _REQUEST_HOST_ACCESS_COMMAND


def request_host_path_access(
    *,
    agent: Agent,
    raw_path: str,
    reason: str,
    command: str | None = None,
    content: str | None = None,
    cwd: str | None = None,
    task_id: str | None = None,
    channel_id: str | None = None,
) -> BossModCliResult:
    """Open the existing in-chat consent card, or fail closed.

    Already-allowlisted paths return success with no card. Denied system
    trees, the filesystem root, and paths with no grantable directory stay
    hard-denied with no card. Pending cards are reused.
    """
    label = (command or "").strip() or _REQUEST_HOST_ACCESS_COMMAND
    token = (raw_path or "").strip()
    if not token:
        return error_result(label, "request_host_access requires a non-empty path", cwd=cwd, executor="virtual")

    from pathlib import Path

    path_obj = Path(token).expanduser()
    if not path_obj.is_absolute():
        return error_result(
            label,
            "request_host_access requires an absolute host path",
            cwd=cwd,
            executor="virtual",
        )
    cleaned = token.replace("\\", "/").strip()
    if cleaned in {"/me", "/projects"} or cleaned.startswith("/me/") or cleaned.startswith("/projects/"):
        return success_result(
            command=label,
            detail=f"Path {token!r} is already inside /me or /projects. Use cli on that path.",
            kind="host_path_already_allowed",
            data={"path": token, "already_allowed": True},
            sections=[("HOST PATH", [f"{token} is already inside the workspace mounts. Use cli."])],
            cwd=cwd,
            executor="virtual",
        )
    if not looks_like_named_absolute_path(token):
        return error_result(label, denial_message(token), cwd=cwd, executor="virtual")
    if _path_already_allowlisted(agent, token, task_id):
        resolved = canonical_host_path(token)
        return success_result(
            command=label,
            detail=f"Host path {resolved!r} is already allowed. Use cli on that path.",
            kind="host_path_already_allowed",
            data={"path": resolved, "already_allowed": True},
            sections=[("HOST PATH", [f"{resolved} is already on the allowlist. Use cli."])],
            cwd=cwd,
            executor="virtual",
        )

    path = canonical_host_path(token)
    grant_root = grantable_host_root(token)
    if grant_root is None:
        return error_result(label, denial_message(token), cwd=cwd, executor="virtual")

    denied = db.find_denied_for_scope(agent.id, path, task_id=task_id)
    if denied is not None:
        return error_result(
            label,
            (
                f"Host-path access denied for {path!r}. "
                "The operator refused this path for the current task."
            ),
            cwd=cwd,
            executor="virtual",
        )

    pending = db.find_pending_for_path(agent.id, path)
    if pending is not None:
        if _clean_channel_id(channel_id) and not pending.channel_id:
            pending = db.bind_consent_channel(pending.id, channel_id) or pending
        return consent_required_result(
            label,
            _consent_message(pending),
            cwd=cwd,
            consent_request=pending,
            reused=True,
        )

    note = (reason or "").strip() or f"Required for command: {label}"
    request = db.create_consent_request(
        agent_id=agent.id,
        path=path,
        grant_root=str(grant_root),
        reason=note,
        command=None if is_request_host_access_command(command) else command,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=_clean_channel_id(channel_id),
    )
    return consent_required_result(
        label,
        _consent_message(request),
        cwd=cwd,
        consent_request=request,
        reused=False,
    )


def handle_named_path_consent(
    *,
    agent: Agent,
    raw_path: str,
    command: str,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None = None,
) -> BossModCliResult:
    """Pause for in-chat consent after a named-path CLI miss, or fail closed."""
    return request_host_path_access(
        agent=agent,
        raw_path=raw_path,
        reason=f"Required for command: {command}",
        command=command,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
    )


def _path_already_allowlisted(agent: Agent, raw_path: str, task_id: str | None) -> bool:
    """Return True when the named path is already inside an allowed host root."""
    from pathlib import Path

    token = host_path_consent_scope.set(ConsentScope(agent_id=agent.id, task_id=task_id))
    try:
        resolved = Path(raw_path).expanduser().resolve()
        return is_within_roots(resolved, named_path_roots(agent.storage_key))
    except OSError:
        return False
    finally:
        host_path_consent_scope.reset(token)


async def resume_host_path_consent(
    request_id: str,
    *,
    decision: ConsentDecision,
    services: Any,
    decision_by: str = "human",
    note: str | None = None,
) -> HostPathConsentRequest | None:
    """Apply Allow once / Always allow / Deny and wake the waiting agent."""
    existing = db.get_consent_request(request_id)
    if existing is None or existing.status != "pending":
        return None

    if decision == "deny":
        updated = db.resolve_consent_request(
            request_id,
            status="denied",
            decision_by=decision_by,
            decision_note=note,
        )
        if updated is None:
            return None
        await _enqueue_resume(updated, status="denied", services=services)
        return updated

    if decision == "allow_once":
        updated = db.resolve_consent_request(
            request_id,
            status="allowed_once",
            decision_by=decision_by,
            decision_note=note,
        )
        if updated is None:
            return None
        db.create_once_grant(
            agent_id=updated.agent_id,
            root=updated.grant_root,
            consent_id=updated.id,
            task_id=updated.task_id,
        )
        await _enqueue_resume(updated, status="allowed_once", services=services)
        return updated

    if decision != "always_allow":
        raise ValueError(f"Unsupported consent decision: {decision}")

    from core.bm_cli.host_roots import validate_host_root
    from core import config

    validate_host_root(existing.grant_root)
    current = _current_host_root_setting()
    merged = normalize_host_root_setting("\n".join([current, existing.grant_root]))
    db.set_setting(SETTING_KEY, merged, SETTING_CATEGORY)
    config.reload()
    updated = db.resolve_consent_request(
        request_id,
        status="always_allowed",
        decision_by=decision_by,
        decision_note=note,
    )
    if updated is None:
        return None
    await _enqueue_resume(updated, status="always_allowed", services=services)
    for sibling in db.list_pending_for_grant_root(updated.grant_root):
        if sibling.id == updated.id:
            continue
        other = db.resolve_consent_request(
            sibling.id,
            status="always_allowed",
            decision_by=decision_by,
            decision_note=note,
        )
        if other is None:
            continue
        await _enqueue_resume(
            other,
            status="always_allowed",
            services=services,
            follow_through=False,
        )
    return updated


def _current_host_root_setting() -> str:
    """Read the live allowlist value from the settings table."""
    from db.crud import query_one

    row = query_one("SELECT value FROM settings WHERE key = $1", [SETTING_KEY])
    if row and row.get("value") is not None:
        return str(row["value"])
    return ""


def _consent_message(request: HostPathConsentRequest) -> str:
    return (
        f"Host-path access needs operator consent in chat for {request.path!r} "
        f"(grant root {request.grant_root!r}). {request.reason}"
    )


async def _enqueue_resume(
    request: HostPathConsentRequest,
    *,
    status: str,
    services: Any,
    follow_through: bool = True,
) -> None:
    payload: dict[str, Any] = {
        "consent_request_id": request.id,
        "command": request.command or "",
        "status": status,
        "path": request.path,
        "task_id": request.task_id,
    }
    channel_id = _clean_channel_id(request.channel_id)
    if channel_id:
        payload["channel_id"] = channel_id
    if status != "denied":
        payload["content"] = request.content
        payload["cwd"] = request.cwd
    await services.enqueue_trigger(
        agent_id=request.agent_id,
        trigger_type="host_path_consent_resolved",
        source_channel="channel" if channel_id else "system",
        payload=payload,
        task_id=request.task_id,
    )
    if follow_through and status in {"allowed_once", "always_allowed"} and channel_id:
        await _post_channel_follow_through(
            request,
            status=status,
            channel_id=channel_id,
            services=services,
        )


def _clean_channel_id(channel_id: str | None) -> str | None:
    """Return a non-empty channel id, or None."""
    token = (channel_id or "").strip()
    return token or None


async def _post_channel_follow_through(
    request: HostPathConsentRequest,
    *,
    status: str,
    channel_id: str,
    services: Any,
) -> None:
    """Post the grant back into the originating channel so the thread is not silent."""
    agent = db.get_agent(request.agent_id)
    if agent is None:
        return
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        return

    from core.agent_loop.channel_rounds import post_agent_channel_share
    from core.runtime.events import runtime_events as manager

    grant_label = (
        "Always allow (for all agents)"
        if status == "always_allowed"
        else "Allow once"
    )
    content = f"{agent.name} can access {request.path}. {grant_label}."
    channel_message, peer_wakes = post_agent_channel_share(
        channel_id=channel_id,
        agent=agent,
        content=content,
        source_channel="channel",
    )
    await manager.broadcast_channel_message(
        channel_id=channel_message["channel_id"],
        content=channel_message["content"],
        author_type=channel_message["author_type"],
        author_name=channel_message["author_name"],
        author_agent_id=channel_message.get("author_agent_id"),
        message_id=channel_message.get("message_id"),
        created_at=channel_message.get("created_at"),
    )
    for wake in peer_wakes:
        await services.enqueue_trigger(
            agent_id=wake["agent_id"],
            trigger_type=wake["trigger_type"],
            source_channel=wake["source_channel"],
            payload=wake["payload"],
        )
