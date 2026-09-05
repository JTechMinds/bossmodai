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

from api.auth import ensure_local_api_token, install_local_api_auth
from api.routes import router as api_router
from core.runtime import runtime_services
from db import init_db, close_connection

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "ui" / "templates"
STATIC_DIR = BASE_DIR / "ui" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
