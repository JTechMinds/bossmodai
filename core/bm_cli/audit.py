"""BossMod AI — Audit logging for BossMod CLI requests."""

from __future__ import annotations

import json

import db
from core.bm_cli.results import trim
from core.bm_cli.types import BossModCliResult


def record_bm_cli_event(
    *,
    agent_id: str,
    command: str,
    content: str | None,
    executor: str,
    cwd_before: str | None,
    cwd_after: str | None,
    policy_tier: str,
    decision: str,
    result: BossModCliResult,
    trigger_type: str | None,
    approval_request_id: str | None = None,
) -> None:
    """Persist a normalized audit event for one BossMod CLI request."""
    stdout_preview, stderr_preview = _build_output_previews(result)
    db.create_bm_cli_event(
        agent_id=agent_id,
        command=command,
        content_present=bool(content and content.strip()),
        executor=executor,
        cwd_before=cwd_before,
        cwd_after=cwd_after,
        policy_tier=policy_tier,
        decision=decision,
        exit_code=result.exit_code,
        result_kind=result.kind,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        changed_paths=_serialize_changed_paths(result),
        trigger_type=trigger_type,
        approval_request_id=approval_request_id,
    )


def _build_output_previews(result: BossModCliResult) -> tuple[str | None, str | None]:
    """Return compact stdout/stderr previews for audit rows."""
    if result.ok:
        return trim(result.prompt_content, limit=1000), None
    data = result.data or {}
    error_text = data.get("error") or data.get("message") or result.detail
    return None, trim(str(error_text), limit=1000)


def _serialize_changed_paths(result: BossModCliResult) -> str | None:
    """Extract changed path information from a CLI result when available."""
    data = result.data or {}
    paths: list[str] = []
    if "path" in data and isinstance(data["path"], str):
        paths.append(data["path"])
    if "paths" in data and isinstance(data["paths"], list):
        paths.extend(str(item) for item in data["paths"])
    if not paths:
        return None
    return json.dumps(paths)
