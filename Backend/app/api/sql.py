"""
SQL Explorer API — Backend
Endpoints for the Agentic SQL Explorer feature.

  GET  /api/servers/{server_id}/sql/discover
  GET  /api/servers/{server_id}/sql/databases
  POST /api/servers/{server_id}/sql/schema
  POST /api/servers/{server_id}/sql/execute
  POST /api/servers/{server_id}/sql/query   ← NL → SQL via Groq
"""

import json
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.organization import PlatformUser
from app.services.command_bridge import send_command_to_agent

logger = logging.getLogger("serverdeck.sql")
settings = get_settings()

router = APIRouter(prefix="/api/servers/{server_id}/sql", tags=["SQL Explorer"])


# ---------- Request / Response Models ----------

class ConnectionParams(BaseModel):
    engine: str                      # postgres | mysql | sqlite
    database: Optional[str] = None
    path: Optional[str] = None       # sqlite file path
    user: Optional[str] = None
    password: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class SchemaRequest(ConnectionParams):
    pass


class ExecuteRequest(ConnectionParams):
    sql: str


class NLQueryRequest(ConnectionParams):
    question: str
    schema: Optional[dict] = None    # { table_name: [{name, type, nullable}] }


# ---------- Helpers ----------

def _agent_params(conn: ConnectionParams) -> dict:
    """Convert connection model to agent-compatible dict (omit None values)."""
    d = conn.model_dump(exclude_none=True)
    return d


async def _call_agent(server_id: str, action: str, params: dict) -> dict:
    result = await send_command_to_agent(server_id, action, params)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "Agent error"))
    return result.get("data", {})


async def _nl_to_sql(question: str, engine: str, database: str, schema: dict) -> str:
    """Convert a natural language question to SQL using Groq Llama."""

    # Build a compact schema description for the prompt
    schema_text_parts = []
    for table, columns in (schema or {}).items():
        col_defs = ", ".join(f"{c['name']} {c['type']}" for c in columns)
        schema_text_parts.append(f"  {table}({col_defs})")
    schema_text = "\n".join(schema_text_parts) if schema_text_parts else "(schema not provided)"

    prompt = f"""You are an expert SQL developer. Convert the user's question into a valid SQL query.

Database engine: {engine}
Database: {database}

Schema:
{schema_text}

User question: {question}

Rules:
- Return ONLY the SQL query — no explanation, no markdown, no backticks.
- Use correct syntax for {engine}.
- For PostgreSQL, use double-quotes for identifiers with capitals.
- Limit results to 500 rows max using LIMIT unless the user asks for all.
"""

    api_key = settings.grok_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="Groq API key not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert SQL developer. Respond with ONLY the SQL query. No markdown, no explanation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        sql = data["choices"][0]["message"]["content"].strip()

        # Strip any accidental markdown fences
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]

        return sql.strip()


# ---------- Endpoints ----------

@router.get("/discover")
async def discover_databases(
    server_id: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Auto-discover all running database engines on the server."""
    params = {}
    if user:
        params["user"] = user
    if password:
        params["password"] = password
    result = await send_command_to_agent(server_id, "sql.discover", params)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "Agent error"))
    return result.get("data", {})


@router.post("/databases")
async def list_databases(
    server_id: str,
    conn: ConnectionParams,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all databases/schemas for a given engine."""
    return await _call_agent(server_id, "sql.list_databases", _agent_params(conn))


@router.post("/schema")
async def get_schema(
    server_id: str,
    conn: SchemaRequest,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch table and column definitions for a database."""
    return await _call_agent(server_id, "sql.get_schema", _agent_params(conn))


@router.post("/execute")
async def execute_sql(
    server_id: str,
    req: ExecuteRequest,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a raw SQL query and return results."""
    params = _agent_params(req)
    return await _call_agent(server_id, "sql.execute", params)


@router.post("/query")
async def natural_language_query(
    server_id: str,
    req: NLQueryRequest,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Convert a natural-language question to SQL and execute it.
    Returns both the generated SQL and the query results.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # 1. Generate SQL from natural language
    sql = await _nl_to_sql(
        question=req.question,
        engine=req.engine,
        database=req.database or req.path or "",
        schema=req.schema or {},
    )

    # 2. Execute the generated SQL against the agent
    exec_params = _agent_params(req)
    exec_params["sql"] = sql

    result = await send_command_to_agent(server_id, "sql.execute", exec_params)
    if result.get("status") == "error":
        # Return SQL so user can debug even if execution fails
        return {
            "sql": sql,
            "error": result.get("error", "Execution failed"),
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    data = result.get("data", {})
    return {
        "sql": sql,
        "columns": data.get("columns", []),
        "rows": data.get("rows", []),
        "row_count": data.get("row_count", 0),
        "error": None,
    }


@router.post("/test-connection")
async def test_connection(
    server_id: str,
    conn: ConnectionParams,
    current_user: PlatformUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test connectivity to a specific database engine."""
    return await _call_agent(server_id, "sql.test_connection", _agent_params(conn))
