"""Nest git Settings status, named credentials, and in-thread Enable / Add / Use."""

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
    label: str | None = None
    match: str | None = None
    credential_id: str | None = None
    is_default: bool | None = None


class NestGitUseBody(BaseModel):
    credential_id: str


@router.get("/nest-git/status")
async def nest_git_status():
    """Redacted Nest git Settings + Shell probe. No secret material."""
    from core.bm_cli.nest_git import nest_git_status as status

    return status()


@router.post("/nest-git/items")
async def create_nest_git_item(body: NestGitCredentialsBody):
    """Add a named credential (label + match + token and/or SSH)."""
    from core.bm_cli.nest_git import nest_git_status
    from core.bm_cli.nest_git_store import add_credential

    try:
        add_credential(
            label=(body.label or "").strip() or "GitHub",
            match=(body.match or "").strip(),
            pat=body.pat,
            ssh_key=body.ssh_key,
            is_default=body.is_default,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return nest_git_status()


@router.put("/nest-git/items/{credential_id}")
async def update_nest_git_item(credential_id: str, body: NestGitCredentialsBody):
    """Edit one named credential. Empty secret fields keep the current value."""
    from core.bm_cli.nest_git import nest_git_status
    from core.bm_cli.nest_git_store import update_credential

    try:
        update_credential(
            credential_id,
            label=body.label,
            match=body.match,
            pat=body.pat,
            ssh_key=body.ssh_key,
            clear_pat=body.clear_pat,
            clear_ssh=body.clear_ssh,
            is_default=body.is_default,
        )
    except KeyError as exc:
        raise HTTPException(404, "Nest git credential not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return nest_git_status()


@router.delete("/nest-git/items/{credential_id}")
async def delete_nest_git_item(credential_id: str):
    """Remove one named credential and its secrets."""
    from core.bm_cli.nest_git import nest_git_status
    from core.bm_cli.nest_git_store import delete_credential

    try:
        delete_credential(credential_id)
    except KeyError as exc:
        raise HTTPException(404, "Nest git credential not found") from exc
    return nest_git_status()


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
            label=body.label,
            match=body.match,
            credential_id=body.credential_id,
            is_default=body.is_default,
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


@router.post("/nest-git/{request_id}/use")
async def use_nest_git_credential(request_id: str, body: NestGitUseBody):
    """Pick a saved credential for this remote and resume the waiting agent."""
    from core.bm_cli.nest_git_consent import resume_nest_git_consent

    try:
        request = await resume_nest_git_consent(
            request_id,
            decision="use",
            services=runtime_services,
            credential_id=body.credential_id,
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
    """Add, rotate, or clear PAT/SSH on the Default / legacy Settings store."""
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
