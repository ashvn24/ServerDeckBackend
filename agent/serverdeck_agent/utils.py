"""
Utility functions for the agent.
"""
import asyncio
import logging

logger = logging.getLogger("serverdeck.agent.utils")


async def run_cmd(command: str, timeout: int = 30) -> dict:
    """
    Run a shell command asynchronously.
    Returns dict with stdout, stderr, returncode.
    Kills process on timeout.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "returncode": -1,
            }
    except Exception as e:
        logger.error(f"run_cmd error: {e}")
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }
