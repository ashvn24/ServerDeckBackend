import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import auth, servers, sites, dashboard, logs, agent_dist
from app.ws import agent_handler, client_handler
from app.services.alerting import check_alerts

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch background alerting task
    alert_task = asyncio.create_task(check_alerts())
    logging.info("ServerDeck API started — alerting task running")
    yield
    # Shutdown
    alert_task.cancel()
    try:
        await alert_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="ServerDeck API",
    description="Lightweight agent-based Linux server management platform",
    version="0.1.0",
    lifespan=lifespan,
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

# WebSocket routers
app.include_router(agent_handler.router)
app.include_router(client_handler.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "serverdeck-api"}
