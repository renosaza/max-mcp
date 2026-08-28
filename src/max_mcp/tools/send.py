import pathlib
import stat
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from ..client import AppCtx
from ..normalize import message_to_dict
from ._paths import HOME, allowed_roots, reject_denied, require_allowed

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

SEND_ROOTS_ENV = "MAX_MCP_SEND_ROOTS"
_DEFAULT_ROOTS = (HOME / "Downloads", HOME / "Documents", pathlib.Path("/tmp"))


def _allowed_roots() -> tuple[pathlib.Path, ...]:
    return allowed_roots(SEND_ROOTS_ENV, _DEFAULT_ROOTS)


def _validate_path(file_path: str) -> pathlib.Path:
    if not isinstance(file_path, str) or not file_path:
        raise ValueError("file_path must be a non-empty string")
    if "\x00" in file_path:
        raise ValueError("file_path contains NUL byte")

    path = pathlib.Path(file_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(f"file_path does not resolve: {exc}") from exc

    if stat.S_ISLNK(path.lstat().st_mode):
        raise ValueError("symlinks are not allowed as file_path")

    st = resolved.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("file_path must point to a regular file")
    if st.st_size > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")

    reject_denied(resolved)
    require_allowed(resolved, _allowed_roots(), SEND_ROOTS_ENV)
    return resolved


def _attachment(path: pathlib.Path) -> Any:
    from pymax import File, Photo, Video

    ext = path.suffix.lower()
    if ext in PHOTO_EXT:
        return Photo(path=str(path))
    if ext in VIDEO_EXT:
        return Video(path=str(path))
    return File(path=str(path))


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def send_message(
        ctx: Context[ServerSession, AppCtx],
        chat_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> dict[str, Any]:
        """Отправить текстовое сообщение или ответ на указанное сообщение MAX."""
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        client = ctx.request_context.lifespan_context.client
        sent = await client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to=reply_to_id,
        )
        return message_to_dict(sent)

    @mcp.tool()
    async def send_file(
        ctx: Context[ServerSession, AppCtx],
        chat_id: int,
        file_path: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Отправить файл, фото или видео из разрешённого локального каталога."""
        path = _validate_path(file_path)
        client = ctx.request_context.lifespan_context.client
        attachment = _attachment(path)
        sent = await client.send_message(
            chat_id=chat_id,
            text=caption or "",
            attachments=[attachment],
        )
        return message_to_dict(sent)
