"""BossMod AI — REST API routers.

Thin aggregator. HTTP groups live in sibling modules; ``from api.routes
import router`` stays the public entry used by ``main.py`` and tests.
"""

from fastapi import APIRouter

from api.routes import agents, cli_policy, company_files, host_path_consent, runtime, settings, tasks, ws

router = APIRouter(prefix="/api")
router.include_router(ws.router)
router.include_router(runtime.router)
router.include_router(agents.router)
router.include_router(company_files.router)
router.include_router(tasks.router)
router.include_router(cli_policy.router)
router.include_router(host_path_consent.router)
router.include_router(settings.router)
