"""
SQL Handler — Agentic SQL Explorer

Provides database discovery, schema inspection, and query execution
for PostgreSQL, MySQL, and SQLite databases running on the server.

Actions:
  - sql.discover          → Detect all running DB engines + sqlite files
  - sql.list_databases    → List all databases for a given engine
  - sql.get_schema        → Get table + column definitions for a database
  - sql.execute           → Execute a SQL query and return rows
  - sql.test_connection   → Test connectivity to a specific engine/database
"""

import logging
import os
import json
import subprocess
import glob
from typing import Optional

logger = logging.getLogger("serverdeck.agent.handlers.sql")


# ---------- Connection Helpers ----------

def _build_psql_env(params: dict) -> dict:
    """Build environment variables for psql authentication."""
    env = os.environ.copy()
    if params.get("password"):
        env["PGPASSWORD"] = params["password"]
    return env


def _psql_cmd(params: dict, query: str, database: str = "postgres", as_json: bool = False) -> dict:
    """Run a psql query and return stdout/stderr/returncode."""
    user = params.get("user", "postgres")
    host = params.get("host", "")
    port = str(params.get("port", 5432))
    db = params.get("database", database)
    env = _build_psql_env(params)

    if as_json:
        # Wrap in json_agg to get column names + typed rows
        wrapped = f"SELECT json_agg(row_to_json(t)) FROM ({query.rstrip(';')}) t;"
        cmd = ["psql", "-U", user, "-d", db, "-t", "-A", "-c", wrapped]
    else:
        cmd = ["psql", "-U", user, "-d", db, "-t", "-A", "-F", "\t", "-c", query]
    if host:
        cmd += ["-h", host]
    cmd += ["-p", port]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"stdout": "", "stderr": "psql not found on this system", "returncode": 1}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "psql command timed out", "returncode": 1}


def _mysql_cmd(params: dict, query: str, database: str = "") -> dict:
    """Run a mysql query and return stdout/stderr/returncode."""
    user = params.get("user", "root")
    host = params.get("host", "127.0.0.1")
    port = str(params.get("port", 3306))
    password = params.get("password", "")

    cmd = ["mysql", f"-u{user}", f"-h{host}", f"-P{port}", "--batch", "--silent", "-e", query]
    if password:
        cmd.append(f"-p{password}")
    if database:
        cmd.append(database)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"stdout": "", "stderr": "mysql not found on this system", "returncode": 1}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "mysql command timed out", "returncode": 1}


def _sqlite_execute(db_path: str, query: str) -> dict:
    """Execute a query against a SQLite database file."""
    import sqlite3
    try:
        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(query)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(row) for row in cur.fetchall()]
        row_count = cur.rowcount if cur.rowcount >= 0 else len(rows)
        con.close()
        return {"columns": columns, "rows": rows, "row_count": row_count, "error": None}
    except sqlite3.Error as e:
        return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}
    except Exception as e:
        return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}


# ---------- Discovery ----------

def _discover_postgres(params: dict) -> Optional[dict]:
    """Check if PostgreSQL is available."""
    try:
        result = subprocess.run(
            ["pg_isready"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return {"engine": "postgres", "status": "running", "host": "localhost", "port": 5432}
    except FileNotFoundError:
        pass
    # Try psql directly
    res = _psql_cmd(params, "SELECT 1")
    if res["returncode"] == 0:
        return {"engine": "postgres", "status": "running", "host": "localhost", "port": 5432}
    return None


def _discover_mysql(params: dict) -> Optional[dict]:
    """Check if MySQL/MariaDB is available."""
    try:
        result = subprocess.run(
            ["mysqladmin", "ping", "-h", "127.0.0.1", "--silent"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return {"engine": "mysql", "status": "running", "host": "127.0.0.1", "port": 3306}
    except FileNotFoundError:
        pass
    return None


def _discover_sqlite() -> list:
    """Find SQLite database files in common locations."""
    search_dirs = ["/var/www", "/home", "/opt", "/srv", "/root", "/tmp"]
    patterns = ["*.db", "*.sqlite", "*.sqlite3"]
    found = []
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for pat in patterns:
            for path in glob.glob(f"{d}/**/{pat}", recursive=True):
                try:
                    import sqlite3
                    con = sqlite3.connect(path, timeout=2)
                    con.execute("SELECT name FROM sqlite_master LIMIT 1")
                    con.close()
                    found.append({
                        "engine": "sqlite",
                        "path": path,
                        "size_bytes": os.path.getsize(path),
                    })
                    if len(found) >= 20:  # Cap at 20 to avoid scanning forever
                        return found
                except Exception:
                    continue
    return found


# ---------- Command Handlers ----------

async def handle_discover(params: dict) -> dict:
    """
    Detect all running database engines on this server.

    Action: sql.discover
    Params: { user?, password?, host? }  (optional pg/mysql credentials)
    """
    logger.info("Discovering database engines...")
    engines = []

    pg = _discover_postgres(params)
    if pg:
        engines.append(pg)

    my = _discover_mysql(params)
    if my:
        engines.append(my)

    sqlite_dbs = _discover_sqlite()
    engines.extend(sqlite_dbs)

    return {"status": "success", "data": {"engines": engines}}


async def handle_list_databases(params: dict) -> dict:
    """
    List all databases/schemas for a given engine.

    Action: sql.list_databases
    Params: { engine: "postgres"|"mysql"|"sqlite", path?, user?, password?, host?, port? }
    """
    engine = params.get("engine", "")

    if engine == "postgres":
        res = _psql_cmd(params, "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;")
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Failed to connect to PostgreSQL"}
        databases = [line for line in res["stdout"].splitlines() if line.strip()]
        return {"status": "success", "data": {"databases": databases}}

    elif engine == "mysql":
        res = _mysql_cmd(params, "SHOW DATABASES;")
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Failed to connect to MySQL"}
        databases = [line for line in res["stdout"].splitlines() if line.strip()]
        return {"status": "success", "data": {"databases": databases}}

    elif engine == "sqlite":
        # For SQLite, the "database" is the file itself
        path = params.get("path", "")
        if not path or not os.path.isfile(path):
            return {"status": "error", "error": f"SQLite file not found: {path}"}
        return {"status": "success", "data": {"databases": [os.path.basename(path)], "path": path}}

    return {"status": "error", "error": f"Unknown engine: {engine}"}


async def handle_get_schema(params: dict) -> dict:
    """
    Get table names and column definitions for a database.

    Action: sql.get_schema
    Params: { engine, database, path? (sqlite), user?, password?, host?, port? }
    """
    engine = params.get("engine", "")
    database = params.get("database", "")

    if engine == "postgres":
        params = {**params, "database": database}
        res = _psql_cmd(
            params,
            """
            SELECT
                t.table_name,
                c.column_name,
                c.data_type,
                c.character_maximum_length,
                c.is_nullable
            FROM information_schema.tables t
            JOIN information_schema.columns c
                ON t.table_name = c.table_name
                AND t.table_schema = c.table_schema
            WHERE t.table_schema = 'public'
            ORDER BY t.table_name, c.ordinal_position;
            """,
            database=database
        )
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Failed to query schema"}
        return {"status": "success", "data": {"schema": _parse_pg_schema(res["stdout"])}}

    elif engine == "mysql":
        res = _mysql_cmd(
            params,
            f"""
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = '{database}'
            ORDER BY TABLE_NAME, ORDINAL_POSITION;
            """,
        )
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Failed to query schema"}
        return {"status": "success", "data": {"schema": _parse_mysql_schema(res["stdout"])}}

    elif engine == "sqlite":
        path = params.get("path", "")
        if not path or not os.path.isfile(path):
            return {"status": "error", "error": f"SQLite file not found: {path}"}
        try:
            import sqlite3
            con = sqlite3.connect(path, timeout=10)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            tables = [row[0] for row in cur.fetchall()]
            schema = {}
            for table in tables:
                cur.execute(f"PRAGMA table_info('{table}');")
                cols = []
                for row in cur.fetchall():
                    cols.append({"name": row[1], "type": row[2], "nullable": not row[3]})
                schema[table] = cols
            con.close()
            return {"status": "success", "data": {"schema": schema}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    return {"status": "error", "error": f"Unknown engine: {engine}"}


async def handle_execute(params: dict) -> dict:
    """
    Execute a SQL query against a specific database.

    Action: sql.execute
    Params: { engine, database, sql, path? (sqlite), user?, password?, host?, port? }
    """
    engine = params.get("engine", "")
    database = params.get("database", "")
    sql = params.get("sql", "").strip()

    if not sql:
        return {"status": "error", "error": "No SQL query provided"}

    logger.info(f"Executing SQL on {engine}/{database}: {sql[:80]}...")

    if engine == "postgres":
        params = {**params, "database": database}
        # Use json_agg mode so column names are preserved
        res = _psql_cmd(params, sql, database=database, as_json=True)
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Query failed"}
        raw = res["stdout"].strip()
        if raw == "" or raw.lower() == "null":
            # Non-SELECT statement (INSERT/UPDATE/DELETE/CREATE etc.)
            return {"status": "success", "data": {"columns": [], "rows": [], "row_count": 0}}
        try:
            json_rows = json.loads(raw)  # list of dicts
            if not json_rows:
                return {"status": "success", "data": {"columns": [], "rows": [], "row_count": 0}}
            columns = list(json_rows[0].keys())
            rows = [[r.get(c) for c in columns] for r in json_rows]
            return {"status": "success", "data": {"columns": columns, "rows": rows, "row_count": len(rows)}}
        except json.JSONDecodeError:
            # Fallback to raw line parsing for DDL output
            return {"status": "success", "data": {"columns": ["output"], "rows": [[l] for l in raw.splitlines()], "row_count": len(raw.splitlines())}}

    elif engine == "mysql":
        res = _mysql_cmd(params, sql, database=database)
        if res["returncode"] != 0:
            return {"status": "error", "error": res["stderr"] or "Query failed"}
        rows, columns = _parse_mysql_output(res["stdout"])
        return {"status": "success", "data": {"columns": columns, "rows": rows, "row_count": len(rows)}}

    elif engine == "sqlite":
        path = params.get("path", "")
        if not path or not os.path.isfile(path):
            return {"status": "error", "error": f"SQLite file not found: {path}"}
        result = _sqlite_execute(path, sql)
        if result["error"]:
            return {"status": "error", "error": result["error"]}
        return {
            "status": "success",
            "data": {
                "columns": result["columns"],
                "rows": result["rows"],
                "row_count": result["row_count"],
            }
        }

    return {"status": "error", "error": f"Unknown engine: {engine}"}


async def handle_test_connection(params: dict) -> dict:
    """
    Test connectivity to a specific database engine.

    Action: sql.test_connection
    Params: { engine, database?, path? (sqlite), user?, password?, host?, port? }
    """
    engine = params.get("engine", "")

    if engine == "postgres":
        res = _psql_cmd(params, "SELECT version();")
        if res["returncode"] == 0:
            version = res["stdout"].strip().split("\n")[0]
            return {"status": "success", "data": {"connected": True, "version": version}}
        return {"status": "error", "error": res["stderr"] or "Connection failed"}

    elif engine == "mysql":
        res = _mysql_cmd(params, "SELECT VERSION();")
        if res["returncode"] == 0:
            version = res["stdout"].strip()
            return {"status": "success", "data": {"connected": True, "version": version}}
        return {"status": "error", "error": res["stderr"] or "Connection failed"}

    elif engine == "sqlite":
        path = params.get("path", "")
        if path and os.path.isfile(path):
            result = _sqlite_execute(path, "SELECT sqlite_version();")
            if not result["error"]:
                version = result["rows"][0][0] if result["rows"] else "unknown"
                return {"status": "success", "data": {"connected": True, "version": f"SQLite {version}"}}
        return {"status": "error", "error": "SQLite file not found or unreadable"}

    return {"status": "error", "error": f"Unknown engine: {engine}"}


# ---------- Output Parsers ----------

def _parse_psql_output(raw: str) -> tuple:
    """Parse tab-separated psql output into (rows, columns)."""
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return [], []
    # The first row may be column headers if using -c with SELECT
    # With -A -t -F\t, we get pure data with no headers — extract from query context
    # We'll treat all lines as data rows and use numeric column headers as fallback
    rows = [line.split("\t") for line in lines]
    if not rows:
        return [], []
    num_cols = max(len(r) for r in rows)
    columns = [f"col{i+1}" for i in range(num_cols)]
    return rows, columns


def _parse_mysql_output(raw: str) -> tuple:
    """Parse tab-separated mysql --batch --silent output into (rows, columns)."""
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return [], []
    # First line is the column headers in --batch mode
    columns = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return rows, columns


def _parse_pg_schema(raw: str) -> dict:
    """Parse psql schema query output into {table: [column_defs]}."""
    schema = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        table, col, dtype = parts[0], parts[1], parts[2]
        nullable = parts[4] == "YES" if len(parts) > 4 else True
        if table not in schema:
            schema[table] = []
        schema[table].append({"name": col, "type": dtype, "nullable": nullable})
    return schema


def _parse_mysql_schema(raw: str) -> dict:
    """Parse mysql schema query output into {table: [column_defs]}."""
    schema = {}
    lines = raw.splitlines()
    if not lines:
        return schema
    for line in lines[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        table, col, dtype = parts[0], parts[1], parts[2]
        nullable = parts[3] == "YES" if len(parts) > 3 else True
        if table not in schema:
            schema[table] = []
        schema[table].append({"name": col, "type": dtype, "nullable": nullable})
    return schema
