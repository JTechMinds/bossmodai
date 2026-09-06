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
