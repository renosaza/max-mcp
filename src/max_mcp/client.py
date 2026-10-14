import asyncio
import os
import pathlib
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from pymax import Client, WebClient

from .config import mobile_client_options, secure_client_config

SESSION_DIR = pathlib.Path.home() / ".max-mcp"
SESSION_FILE = "session.db"
KIND_FILE = "session.kind"
PHONE_FILE = "session.phone"
START_TIMEOUT_SECONDS = 30


@dataclass
class AppCtx:
    client: Any  # WebClient | Client: общий публичный интерфейс PyMax


def _check_session_dir() -> None:
    st = SESSION_DIR.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"{SESSION_DIR} is a symlink; refusing to use")
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"{SESSION_DIR} is not a directory")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{SESSION_DIR} is not owned by current user")
    if st.st_mode & 0o077:
        SESSION_DIR.chmod(0o700)


def _check_session_file(path: pathlib.Path) -> None:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"{path} is a symlink; refusing to use")
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"{path} is not a regular file")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{path} is not owned by current user")
    if st.st_mode & 0o077:
        path.chmod(0o600)


def _read_secret(path: pathlib.Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink():
        raise RuntimeError(f"{path} is a symlink; refusing to read")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        data = os.read(fd, 4096)
    finally:
        os.close(fd)
    return data.decode("utf-8").strip()


def _read_kind() -> tuple[str, str | None]:
    # Отсутствующий marker трактуем как web для совместимости со старыми сессиями.
    kind = _read_secret(SESSION_DIR / KIND_FILE) or "web"
    if kind not in {"web", "sms"}:
        raise RuntimeError(
            f"Unknown session kind {kind!r}; remove {SESSION_DIR / KIND_FILE} and re-login"
        )
    phone = _read_secret(SESSION_DIR / PHONE_FILE)
    return kind, phone


def _build_client() -> Any:
    kind, phone = _read_kind()
    if kind == "sms":
        if not phone:
            raise RuntimeError(
                "SMS session marker found but phone is missing. Re-login with: "
                "max-mcp-login login-sms --phone +7..."
            )
        return Client(
            phone=phone,
            extra_config=secure_client_config(),
            **mobile_client_options(),
            work_dir=str(SESSION_DIR),
            session_name=SESSION_FILE,
        )
    return WebClient(
        work_dir=str(SESSION_DIR),
        session_name=SESSION_FILE,
        extra_config=secure_client_config(),
    )


async def _wait_until_ready(
    client_task: asyncio.Task[Any],
    ready: asyncio.Event,
    timeout: float = START_TIMEOUT_SECONDS,
) -> None:
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {client_task, ready_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError(f"MAX client did not become ready in {timeout:g} seconds")
        if client_task in done:
            # Propagate the original exception, if any. A clean early return is also an error.
            await client_task
            raise RuntimeError("MAX client stopped before the MCP server became ready")
        await ready_task
    finally:
        if not ready_task.done():
            ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppCtx]:
    session_path = SESSION_DIR / SESSION_FILE
    if not session_path.exists():
        raise RuntimeError(
            "MAX session missing. Run one of:\n"
            "  max-mcp-login login-qr\n"
            "  max-mcp-login login-sms --phone +7..."
        )

    _check_session_dir()
    _check_session_file(session_path)

    client = _build_client()
    ready = asyncio.Event()

    @client.on_start()
    async def _ready(_client: Any) -> None:
        ready.set()

    task = asyncio.create_task(client.start(), name="max-mcp-pymax-client")
    try:
        await _wait_until_ready(task, ready)
        yield AppCtx(client=client)
    finally:
        await asyncio.gather(client.stop(), return_exceptions=True)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
