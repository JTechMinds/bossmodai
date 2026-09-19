"""Nest git Settings status and in-thread Enable / Add PAT/SSH."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.websocket import manager
from core.models.nest_git import NEST_GIT_EMPTY_CREDS, NEST_GIT_ENABLED_NOTE
from core.runtime import runtime_services

router = APIRouter()


class NestGitCredentialsBody(BaseModel):
    pat: str | None = None
    ssh_key: str | None = None
    clear_pat: bool = False
    clear_ssh: bool = False


@router.get("/nest-git/status")
async def nest_git_status():
    """Redacted Nest git Settings + Shell probe. No secret material."""
    from core.bm_cli.nest_git import nest_git_status as status

    return status()


@router.post("/nest-git/{request_id}/enable")
async def enable_nest_git(request_id: str):
    """Probe host git, persist Enable On only if it passes, then resume."""
    from core.bm_cli.nest_git_consent import NestGitProbeError, resume_nest_git_consent

    try:
        request = await resume_nest_git_consent(
            request_id,
            decision="enable",
            services=runtime_services,
        )
    except NestGitProbeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    card = request.as_card()
    await manager.broadcast_activity(
        event="nest_git_enabled",
        detail=NEST_GIT_ENABLED_NOTE,
        extra={"host_path_consent": card},
    )
    return card


@router.post("/nest-git/{request_id}/credentials")
async def add_nest_git_credentials(request_id: str, body: NestGitCredentialsBody):
    """Write PAT/SSH to Settings (bm1 wrap) and resume the waiting agent."""
    from core.bm_cli.nest_git_consent import resume_nest_git_consent

    try:
        request = await resume_nest_git_consent(
            request_id,
            decision="credentials",
            services=runtime_services,
            pat=body.pat,
            ssh_key=body.ssh_key,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if request is None:
        raise HTTPException(404, "Consent request not found or already resolved")
    card = request.as_card()
    await manager.broadcast_activity(
        event="nest_git_credentials",
        detail=NEST_GIT_ENABLED_NOTE,
        extra={"host_path_consent": card},
    )
    return card


@router.put("/nest-git/credentials")
async def put_nest_git_credentials(body: NestGitCredentialsBody):
    """Add, rotate, or clear PAT/SSH on the one Settings store."""
    from core.bm_cli.nest_git import nest_git_status, write_nest_git_secret
    from core.models.nest_git import NEST_GIT_PAT_KEY, NEST_GIT_SSH_KEY

    if not body.clear_pat and not body.clear_ssh and not (body.pat or "").strip() and not (body.ssh_key or "").strip():
        raise HTTPException(400, NEST_GIT_EMPTY_CREDS)
    if body.clear_pat:
        write_nest_git_secret(NEST_GIT_PAT_KEY, "")
    elif (body.pat or "").strip():
        write_nest_git_secret(NEST_GIT_PAT_KEY, body.pat.strip())
    if body.clear_ssh:
        write_nest_git_secret(NEST_GIT_SSH_KEY, "")
    elif (body.ssh_key or "").strip():
        write_nest_git_secret(NEST_GIT_SSH_KEY, body.ssh_key.strip())
    return nest_git_status()
