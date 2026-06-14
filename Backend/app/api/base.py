from app.api import auth, servers, sites, dashboard, logs, agent_dist, users, folders, audit, admin, tickets, alerts, sql
from app.ws import agent_handler, client_handler

from fastapi import APIRouter

print("api base imported")
app = APIRouter()
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
app.include_router(alerts.router)
app.include_router(sql.router)

# WebSocket routers
app.include_router(agent_handler.router)
app.include_router(client_handler.router)