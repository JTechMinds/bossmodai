"""BossMod AI — FastAPI backend.

Serves the UI via Jinja2 templates, static files, REST API,
and WebSocket connections. Launched by the Tauri desktop shell
or directly via `uv run python main.py` for development.
"""

import hashlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.auth import ensure_local_api_token, install_local_api_auth, install_settings_refresh
from api.routes import router as api_router
from core.agent_repository import agent_repository
from core.runtime import runtime_services
from db import init_db, close_connection

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "ui" / "templates"
STATIC_DIR = BASE_DIR / "ui" / "static"


def _sweep_stale_pending_attachments() -> None:
    """Delete uploads that were never sent within ``bossmod.attach.pending_ttl_hours``.

    Each file is removed before its row, so a file that cannot be removed
    keeps its row and is retried on the next start. A file that is already
    gone is logged and only loses its row.

    Raises:
        core.config.ConfigError: The TTL setting is missing or not an integer.
    """
    from datetime import datetime, timedelta, timezone

    from core import config
    from db import attachments as db_att

    ttl_hours = config.require_int("bossmod.attach.pending_ttl_hours")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    removed = 0
    for att in db_att.list_stale_pending(cutoff):
        try:
            os.remove(att.storage_path)
        except FileNotFoundError:
            logger.warning("Stale attachment %s had no file at %s", att.id, att.storage_path)
        except OSError as exc:
            logger.warning("Could not remove stale attachment file %s: %s", att.storage_path, exc)
            continue
        if db_att.delete_pending_attachment(att.id) is not None:
            removed += 1
    logger.info("Swept %d stale pending attachment(s)", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Once per app start: after init_db (ledger seeding and identity backfill
    # know every live agent) and before the runtime worker starts. Not inside
    # init_db, which runs again on every runtime start.
    agent_repository.purge_orphans()
    # Also once per app start: an upload abandoned in a draft is only stale
    # after its TTL, so this cannot race a send in progress.
    _sweep_stale_pending_attachments()
    ensure_local_api_token()
    from api.websocket import manager

    runtime_services.set_event_sink(manager)
    await runtime_services.start()

    try:
        from integrations import telegram as tg
        telegram_bridge = await tg.start(services=runtime_services, broadcast_manager=manager)
        if telegram_bridge:
            runtime_services.set_telegram_bridge(telegram_bridge)
    except ImportError:
        pass
    except Exception:
        logger.warning("Telegram bot failed to start", exc_info=True)

    yield

    try:
        from integrations import telegram as tg
        await tg.stop()
    except (ImportError, Exception):
        pass

    await runtime_services.stop()
    close_connection()


app = FastAPI(
    title="BossMod AI",
    description="Self-hosted platform for autonomous AI agent teams",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(api_router)
install_local_api_auth(app)
install_settings_refresh(app)

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def static_url(path: str) -> str:
    """Return a cache-busted URL for a static file."""
    file_path = STATIC_DIR / path
    try:
        content_hash = hashlib.md5(file_path.read_bytes()).hexdigest()[:8]
        return f"/static/{path}?h={content_hash}"
    except FileNotFoundError:
        return f"/static/{path}"


templates.env.globals["static_url"] = static_url


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"local_api_token": ensure_local_api_token()},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


HOST = os.environ.get("BOSSMOD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOSSMOD_PORT", "38471"))

if __name__ == "__main__":
    reload = "--reload" in sys.argv
    uvicorn.run(
        "main:app" if reload else app,
        host=HOST,
        port=PORT,
        reload=reload,
        reload_dirs=[str(BASE_DIR)] if reload else None,
    )
