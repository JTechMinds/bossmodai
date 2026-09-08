"""In-chat host-path consent decisions."""

from fastapi import APIRouter, HTTPException

from api.websocket import manager
from core.bm_cli.host_path_consent import resume_host_path_consent
from core.runtime import runtime_services

router = APIRouter()


@router.post("/host-path-consent/{request_id}/allow-once")
async def allow_once_host_path(request_id: str):
    """Grant the requested root for this turn or task only."""
    request = await resume_host_path_consent(
        request_id,
        decision="allow_once",
        services=runtime_services,
    )
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="host_path_consent_allowed_once",
        detail=f"Host path allowed once: {request.path}",
    )
    return request


@router.post("/host-path-consent/{request_id}/always-allow")
async def always_allow_host_path(request_id: str):
    """Add the grant root to the operator host-roots allowlist."""
    request = await resume_host_path_consent(
        request_id,
        decision="always_allow",
        services=runtime_services,
    )
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="host_path_consent_always_allowed",
        detail=f"Host path always allowed: {request.grant_root}",
    )
    return request


@router.post("/host-path-consent/{request_id}/deny")
async def deny_host_path(request_id: str):
    """Refuse the requested host path (fail-closed)."""
    request = await resume_host_path_consent(
        request_id,
        decision="deny",
        services=runtime_services,
    )
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="host_path_consent_denied",
        detail=f"Host path denied: {request.path}",
    )
    return request


@router.post("/workspace-preference/{request_id}/clone")
async def clone_workspace_preference(request_id: str):
    """Clone the named host path into the agent's /me workspace."""
    from core.bm_cli.workspace_preference import resume_workspace_preference

    try:
        request = await resume_workspace_preference(
            request_id,
            decision="clone",
            services=runtime_services,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="workspace_preference_cloned",
        detail=f"Cloned host path into workspace: {request.clone_dest or request.path}",
    )
    return request


@router.post("/workspace-preference/{request_id}/branch")
async def branch_workspace_preference(request_id: str):
    """Clone into /me and create a git branch. Hidden when the host path is not a git repo."""
    import db
    from core.bm_cli.workspace_preference import resume_workspace_preference

    existing = db.get_consent_request(request_id)
    if existing is not None and not existing.is_git:
        raise HTTPException(409, "Make a branch is only available for git repositories.")
    try:
        request = await resume_workspace_preference(
            request_id,
            decision="branch",
            services=runtime_services,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="workspace_preference_branched",
        detail=f"Branched host path into workspace: {request.clone_dest or request.path}",
    )
    return request


@router.post("/workspace-preference/{request_id}/edit-host")
async def edit_host_workspace_preference(request_id: str):
    """Allow editing the named host path directly (not advised)."""
    from core.bm_cli.workspace_preference import resume_workspace_preference

    request = await resume_workspace_preference(
        request_id,
        decision="edit_host",
        services=runtime_services,
    )
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="workspace_preference_edit_host",
        detail=f"Edit host directly: {request.path}",
    )
    return request


@router.post("/workspace-preference/{request_id}/cancel")
async def cancel_workspace_preference(request_id: str):
    """Cancel workspace preference (fail-closed; no host writes)."""
    from core.bm_cli.workspace_preference import resume_workspace_preference

    request = await resume_workspace_preference(
        request_id,
        decision="cancel",
        services=runtime_services,
    )
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    await manager.broadcast_activity(
        event="workspace_preference_cancelled",
        detail=f"Workspace preference cancelled: {request.path}",
    )
    return request
