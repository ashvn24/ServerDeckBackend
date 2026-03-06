"""
Process Handler — list and kill processes.
"""
import logging
import signal
import psutil

logger = logging.getLogger("serverdeck.agent.process")


async def handle_list(params: dict) -> dict:
    """List top processes by CPU/RAM."""
    procs = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "username"]):
        try:
            info = proc.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "cpu_percent": info["cpu_percent"] or 0,
                "memory_mb": round((info["memory_info"].rss if info["memory_info"] else 0) / (1024 * 1024), 1),
                "username": info["username"] or "unknown",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Sort by CPU descending, return top 50
    procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
    return {"processes": procs[:50]}


async def handle_kill(params: dict) -> dict:
    """Kill a process by PID."""
    pid = params["pid"]
    sig = params.get("signal", "SIGTERM")

    try:
        sig_num = getattr(signal, sig, signal.SIGTERM)
        proc = psutil.Process(pid)
        proc.send_signal(sig_num)
        return {"pid": pid, "status": "killed", "signal": sig}
    except psutil.NoSuchProcess:
        return {"error": f"Process {pid} not found"}
    except psutil.AccessDenied:
        return {"error": f"Access denied for PID {pid}"}
    except Exception as e:
        return {"error": str(e)}
