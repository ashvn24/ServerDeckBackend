import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from alembic.config import Config
from alembic import command

from app.config import get_settings
from app.api import auth, servers, sites, dashboard, logs, agent_dist, users, folders, audit, admin, tickets
from app.ws import agent_handler, client_handler
from app.services.alerting import check_alerts

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def run_migrations():
    def _run():
        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
    await asyncio.to_thread(_run)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: run DB migrations then launch background alerting task
    logger.info("Running database migrations...")
    await run_migrations()
    logger.info("Database migrations complete")
    alert_task = asyncio.create_task(check_alerts())
    logger.info("ServerDeck API started — alerting task running")
    yield
    # Shutdown
    alert_task.cancel()
    try:
        await alert_task
    except asyncio.CancelledError:
        pass


from app.services.tenant import resolve_tenant

app = FastAPI(
    title="ServerDeck API",
    description="Lightweight agent-based Linux server management platform",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(resolve_tenant)]
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST API routers
app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(sites.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(agent_dist.router)
app.include_router(users.router)
app.include_router(folders.router)
app.include_router(audit.router)
app.include_router(admin.router)
app.include_router(tickets.router)

# WebSocket routers
app.include_router(agent_handler.router)
app.include_router(client_handler.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "serverdeck-api"}
