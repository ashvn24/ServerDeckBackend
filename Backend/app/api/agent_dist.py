import io
import os
import tarfile
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(tags=["agent-distribution"])

# The agent source code lives at: d:\ServerDeck\agent\serverdeck_agent\
# Relative to the Backend directory, it's: ../agent/
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "agent"


@router.get("/install.sh")
async def serve_install_script():
    """Serve the agent install script (called by: curl -s .../install.sh | bash)."""
    script_path = AGENT_ROOT / "install.sh"
    if not script_path.exists():
        return {"error": "install.sh not found"}
    return FileResponse(
        path=str(script_path),
        media_type="text/plain",
        filename="install.sh",
    )


@router.get("/api/agent/download")
async def download_agent():
    """Serve the agent code as a tar.gz archive.
    
    The archive contains:
      serverdeck_agent/
        __init__.py
        config.py
        connection.py
        main.py
        system_info.py
        utils.py
        handlers/
          __init__.py
          nginx.py
          systemd.py
          pm2.py
          ssl.py
          logs.py
          firewall.py
          process.py
    """
    agent_pkg = AGENT_ROOT / "serverdeck_agent"
    if not agent_pkg.exists():
        return {"error": "Agent package not found"}

    # Build tar.gz in memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(agent_pkg):
            for filename in filenames:
                if filename.endswith((".py", ".json")):
                    full_path = os.path.join(dirpath, filename)
                    # Archive name relative to agent/ directory
                    arcname = os.path.relpath(full_path, AGENT_ROOT)
                    tar.add(full_path, arcname=arcname)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={
            "Content-Disposition": "attachment; filename=serverdeck-agent.tar.gz"
        },
    )
