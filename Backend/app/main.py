import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.services.alert_service import check_alerts
from app.api.base import app as api_app

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch the background alerting task. Schema migrations are NOT
    # run here — apply them at deploy time with migrate_tenants.py.
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
    dependencies=[Depends(resolve_tenant)],
    swagger_ui_parameters={"defaultModelsExpandDepth": -1}
)

app.include_router(api_app)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "serverdeck-api"}
