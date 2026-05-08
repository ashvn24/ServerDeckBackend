"""
Terminal Handler — manages PTY-backed shell sessions for SSH-like access.

Each session forks a shell attached to a pseudo-terminal. Output from the PTY
is read in a background task and forwarded to the portal via the supplied
`send_fn` coroutine. Input from the portal is written directly to the master fd.
"""
import asyncio
import fcntl
import logging
import os
import pty
import shutil
import signal
import struct
import termios

logger = logging.getLogger("serverdeck.agent.terminal")


def _resolve_shell(preferred: str | None) -> str:
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend([os.environ.get("SHELL"), "/bin/bash", "/bin/sh"])
    for sh in candidates:
        if sh and os.path.exists(sh) and os.access(sh, os.X_OK):
            return sh
    found = shutil.which("bash") or shutil.which("sh")
    if found:
        return found
    return "/bin/sh"


class TerminalSession:
    def __init__(self, session_id: str, send_fn, shell: str | None = None,
                 cols: int = 80, rows: int = 24):
        self.session_id = session_id
        self.send_fn = send_fn
        self.shell = _resolve_shell(shell)
        self.cols = cols
        self.rows = rows
        self.master_fd: int | None = None
        self.pid: int | None = None
        self.closed = False
        self._read_task: asyncio.Task | None = None

    def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            # Child: replace ourselves with the user's shell.
            os.environ.setdefault("TERM", "xterm-256color")
            try:
                os.execvp(self.shell, [self.shell, "-i"])
            except OSError:
                os._exit(1)
        self.pid = pid
        self.master_fd = fd
        self._set_winsize(self.rows, self.cols)
        self._read_task = asyncio.create_task(self._read_loop())

    def _set_winsize(self, rows: int, cols: int) -> None:
        if self.master_fd is None:
            return
        try:
            fcntl.ioctl(
                self.master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError:
            pass

    def write(self, data: str) -> None:
        if self.master_fd is None or self.closed:
            return
        try:
            os.write(self.master_fd, data.encode("utf-8"))
        except OSError as e:
            logger.warning(f"terminal write failed: {e}")

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self._set_winsize(rows, cols)

    async def _read_loop(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            while not self.closed and self.master_fd is not None:
                try:
                    data = await loop.run_in_executor(None, self._read_chunk)
                except Exception as e:
                    logger.warning(f"terminal read failed: {e}")
                    break
                if not data:
                    break
                try:
                    await self.send_fn({
                        "type": "terminal_output",
                        "id": self.session_id,
                        "data": data.decode("utf-8", "replace"),
                    })
                except Exception as e:
                    logger.error(f"terminal send failed: {e}")
                    break
        finally:
            await self._cleanup()

    def _read_chunk(self) -> bytes:
        if self.master_fd is None:
            return b""
        try:
            return os.read(self.master_fd, 4096)
        except OSError:
            return b""

    async def _cleanup(self) -> None:
        if self.closed:
            return
        self.closed = True
        fd = self.master_fd
        self.master_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                # Reap to prevent zombies; best-effort, non-blocking.
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: os.waitpid(self.pid, os.WNOHANG)
                )
            except (OSError, ChildProcessError):
                pass
        try:
            await self.send_fn({
                "type": "terminal_closed",
                "id": self.session_id,
            })
        except Exception:
            pass

    async def close(self) -> None:
        await self._cleanup()
