"""Settings, AI connections, connection test, and personalities."""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.redaction import serialize_connection, serialize_setting, serialize_settings
from api.routes._shared import (
    _RUNTIME_CONTRACT_KEYS,
    _available_folder_opener_options,
    _validate_authored_prompt_template,
)
from api.websocket import manager
from core import config
from core.llm.connection_url import ConnectionUrlError, validate_connection_test_url
from core.llm.template_engine import TemplateError
from core.models import (
    AIConnectionCreate,
    AIConnectionUpdate,
    AIPersonality,
    AIPersonalityCreate,
    AIPersonalityUpdate,
)
from core.runtime import runtime_services
from integrations.telegram.auth import parse_allowed_user_ids
import db

router = APIRouter()


# ─── Settings ───

@router.get("/settings")
async def get_settings(category: str | None = None):
    return serialize_settings(db.get_settings(category))


@router.get("/settings/desktop-open-folder-options")
async def get_desktop_open_folder_options():
    return {
        "current": config.get("desktop_open_folder_handler"),
        "options": _available_folder_opener_options(),
    }


@router.post("/settings/reseed")
async def reseed_settings():
    """Force all seed settings back to their defaults."""
    db.force_reseed()
    config.reload()
    return {"status": "ok", "detail": "All seed settings reset to defaults"}


@router.post("/settings/reseed-application")
async def reseed_application():
    """Recreate the brand-new application database from the current schema.

    This clears DB state and agent desk workspaces (/me). Shared project files (/projects) are preserved.
    """
    await runtime_services.reseed_application_data()
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="application_reseeded",
        detail="Application data reseeded from the current schema defaults (project files preserved)",
    )
    return {"status": "ok", "detail": "Application database recreated from current schema defaults"}


@router.post("/settings/{key}/reset")
async def reset_setting_to_default(key: str):
    """Reset one seeded setting back to its default value."""
    try:
        result = db.reset_setting_to_seed(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config.reload()
    return serialize_setting(result)


def _validate_telegram_settings(key: str, value: str) -> None:
    """Reject Telegram enablement without a usable allowlist (SEC-P0-01)."""
    if key == "telegram_enabled" and value == "true":
        if not parse_allowed_user_ids(config.get("telegram_allowed_user_ids")):
            raise HTTPException(
                400,
                "Add at least one Telegram user ID before enabling the bot. "
                "An empty allowlist is deny-all and the bot will not start.",
            )
    if key == "telegram_allowed_user_ids":
        if not parse_allowed_user_ids(value) and config.get("telegram_enabled") == "true":
            raise HTTPException(
                400,
                "Cannot clear the Telegram allowlist while the bot is enabled. "
                "Disable Telegram first, or keep at least one user ID.",
            )


@router.put("/settings/{key}")
async def set_setting(key: str, value: str, category: str = "general"):
    if key == "system_prompt_template" or key in _RUNTIME_CONTRACT_KEYS.values():
        try:
            _validate_authored_prompt_template(value)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    _validate_telegram_settings(key, value)
    if key == "workspace_host_roots":
        from core.bm_cli.host_roots import SETTING_CATEGORY, normalize_host_root_setting

        try:
            value = normalize_host_root_setting(value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        category = SETTING_CATEGORY
    try:
        result = db.set_setting(key, value, category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config.reload()  # Invalidate cache so changes take effect immediately
    return serialize_setting(result)


# ─── AI Connections CRUD ───

@router.get("/connections")
async def list_connections():
    return [serialize_connection(conn) for conn in db.list_connections()]


@router.get("/connections/{connection_id}")
async def get_connection(connection_id: str):
    conn = db.get_connection_by_id(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return serialize_connection(conn)


@router.post("/connections", status_code=201)
async def create_connection(body: AIConnectionCreate):
    return serialize_connection(db.create_connection(
        name=body.name,
        api_base_url=body.api_base_url,
        api_key=body.api_key,
        model=body.model,
        extra_body=body.extra_body,
    ))


@router.patch("/connections/{connection_id}")
async def update_connection(connection_id: str, body: AIConnectionUpdate):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    if fields.get("api_key") == "":
        fields.pop("api_key", None)
    conn = db.update_connection(connection_id, **fields)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return serialize_connection(conn)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str):
    if not db.delete_connection(connection_id):
        raise HTTPException(404, "Connection not found")


class TestConnectionBody(BaseModel):
    api_base_url: str
    api_key: str | None = None
    model: str | None = None
    connection_id: str | None = None


@router.post("/connections/test")
async def test_connection(body: TestConnectionBody):
    """Test an AI connection by hitting GET {base_url}/models.

    Verifies the host is reachable, auth works, and the response
    is OpenAI-compatible. Optionally checks the model exists.
    """
    try:
        base = validate_connection_test_url(body.api_base_url).rstrip("/")
    except ConnectionUrlError as exc:
        return {"ok": False, "error": str(exc)}
    if base.endswith("/chat/completions") or base.endswith("/completions"):
        return {
            "ok": False,
            "error": "Use the API base URL, not a completions endpoint. Example: https://host/v1",
        }

    api_key = body.api_key
    if not api_key and body.connection_id:
        stored = db.get_connection_by_id(body.connection_id)
        if stored is not None:
            api_key = stored.api_key

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(base + "/models", headers=headers)
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection failed — check the URL"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Connection timed out after 10s"}
    except Exception as exc:
        return {"ok": False, "error": f"Request failed: {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "error": "Authentication failed — check your API key"}
    if resp.status_code == 403:
        return {"ok": False, "error": "Access denied — API key lacks permissions"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Server returned {resp.status_code}"}

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": "Response is not valid JSON"}

    models_list = data.get("data")
    if not isinstance(models_list, list):
        return {"ok": False, "error": "Response missing 'data' array — may not be OpenAI-compatible"}

    model_ids = [m.get("id", "") for m in models_list]

    if body.model and body.model not in model_ids:
        return {
            "ok": True,
            "warning": f"Connected, but model '{body.model}' not found in {len(model_ids)} available models",
            "models": model_ids[:20],
        }

    return {
        "ok": True,
        "models_count": len(model_ids),
        "models": model_ids[:20],
    }


# ─── AI Personalities CRUD ───

@router.get("/personalities")
async def list_personalities() -> list[AIPersonality]:
    return db.list_personalities()


@router.get("/personalities/{personality_id}")
async def get_personality(personality_id: str) -> AIPersonality:
    p = db.get_personality(personality_id)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.post("/personalities", status_code=201)
async def create_personality(body: AIPersonalityCreate) -> AIPersonality:
    try:
        _validate_authored_prompt_template(body.prompt_template)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return db.create_personality(
        name=body.name,
        prompt_template=body.prompt_template,
    )


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: AIPersonalityUpdate) -> AIPersonality:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    prompt_template = fields.get("prompt_template")
    if isinstance(prompt_template, str):
        try:
            _validate_authored_prompt_template(prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    p = db.update_personality(personality_id, **fields)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.delete("/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str):
    if not db.delete_personality(personality_id):
        raise HTTPException(404, "Personality not found")
