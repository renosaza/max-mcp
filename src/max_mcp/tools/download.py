import mimetypes
import os
import pathlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import StrictInt

from ..client import AppCtx
from ..normalize import attach_to_dict, enum_text
from ._common import parse_message_id
from ._paths import (
    HOME,
    allowed_roots,
    check_directory,
    open_directory,
    publish_file,
    safe_filename,
)

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
CHUNK_BYTES = 256 * 1024

DOWNLOAD_ROOTS_ENV = "MAX_MCP_DOWNLOAD_ROOTS"
_DEFAULT_ROOTS = (HOME / "Downloads",)

DEFAULT_EXT = {"PHOTO": ".jpg", "VIDEO": ".mp4", "AUDIO": ".m4a"}
_CDN_DOMAINS = ("max.ru", "oneme.ru", "okcdn.ru", "mycdn.me")


@dataclass
class _Source:
    url: str
    filename: str
    expected_size: int | None
    external: str | None = None


def _download_roots() -> tuple[pathlib.Path, ...]:
    return allowed_roots(DOWNLOAD_ROOTS_ENV, _DEFAULT_ROOTS)


def _field(attach: Any, name: str) -> Any:
    if isinstance(attach, dict):
        return attach.get(name)
    return getattr(attach, name, None)


async def _resolve_source(
    client: Any,
    chat_id: int,
    message_id: int,
    attach: Any,
    kind: str,
) -> _Source:
    """Находит ссылку на байты вложения — своим способом для каждого типа."""
    if kind == "FILE":
        file_id = _field(attach, "file_id")
        if file_id is None:
            raise ValueError("file attachment has no file_id")
        request = await client.get_file_by_id(chat_id, message_id, file_id)
        url = _field(request, "url")
        if not url:
            raise RuntimeError("MAX did not return a download url for this file")
        name = safe_filename(_field(attach, "name") or "", f"file_{file_id}")
        return _Source(url=url, filename=name, expected_size=_field(attach, "size"))

    if kind == "VIDEO":
        video_id = _field(attach, "video_id")
        if video_id is None:
            raise ValueError("video attachment has no video_id")
        request = await client.get_video_by_id(chat_id, message_id, video_id)
        url = _field(request, "url")
        external = _field(request, "external")
        external = external if isinstance(external, str) else None
        if not url:
            raise RuntimeError(
                "MAX did not return a direct url for this video"
                + ("; open the external source in MAX" if external else "")
            )
        return _Source(
            url=url,
            filename=f"video_{video_id}",
            expected_size=None,
            external=external,
        )

    if kind == "PHOTO":
        url = _field(attach, "base_url")
        if not url:
            raise RuntimeError("photo attachment has no base_url")
        photo_id = _field(attach, "photo_id") or "photo"
        return _Source(url=url, filename=f"photo_{photo_id}", expected_size=None)

    if kind == "AUDIO":
        url = _field(attach, "url")
        if not url:
            raise RuntimeError("audio attachment has no url")
        audio_id = _field(attach, "audio_id") or "audio"
        return _Source(url=url, filename=f"audio_{audio_id}", expected_size=None)

    raise ValueError(
        f"attachment of type {kind} carries no downloadable payload; "
        "downloadable types are FILE, VIDEO, PHOTO, AUDIO"
    )


def _sniff_extension(head: bytes, kind: str) -> str | None:
    """Узнаёт формат по первым байтам файла.

    MAX отдаёт картинки в WebP, а голосовые в Ogg/Opus, объявляя их обычными
    photo и audio. Расширение, выведенное из типа вложения, в этих случаях
    врёт, и дальше по цепочке файл открывают неверным разбором.
    """
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"OggS"):
        return ".ogg"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith(b"M4A"):
            return ".m4a"
        if brand.startswith(b"qt"):
            return ".mov"
        return ".m4a" if kind == "AUDIO" else ".mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if head.startswith(b"%PDF-"):
        return ".pdf"
    return None


def _extension_for(kind: str, content_type: str | None, head: bytes) -> str:
    """Выбирает расширение: сигнатура байт, затем Content-Type, затем тип вложения."""
    sniffed = _sniff_extension(head, kind)
    if sniffed:
        return sniffed
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime and mime != "application/octet-stream":
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    return DEFAULT_EXT.get(kind, "")


def _with_extension(
    filename: str, kind: str, content_type: str | None, head: bytes
) -> str:
    """Дописывает расширение, когда имя пришло без него.

    Имя файлового вложения задал отправитель: если он прислал его без
    расширения, своё не выдумывается сверх того, что видно по байтам.
    """
    if pathlib.Path(filename).suffix:
        return filename
    return filename + _extension_for(kind, content_type, head)


def _validate_url(url: str) -> None:
    """Разрешает HTTPS-ссылки MAX/CDN, включая каждый переход редиректа."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and any(
                host == domain or host.endswith("." + domain) for domain in _CDN_DOMAINS
            )
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("attachment URL must use HTTPS on an approved MAX CDN")


async def _fetch(
    url: str, directory: pathlib.Path, source: _Source, kind: str, *, directory_fd: int
) -> dict[str, Any]:
    _validate_url(url)
    if source.expected_size is not None and source.expected_size > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"attachment exceeds {MAX_DOWNLOAD_BYTES} bytes")
    timeout = aiohttp.ClientTimeout(total=600, sock_connect=30, sock_read=120)
    target = None
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            for _ in range(6):
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("attachment redirect has no destination")
                        url = urljoin(str(response.url), location)
                        _validate_url(url)
                        continue
                    if response.status != 200:
                        detail = (
                            "; open the external video in MAX"
                            if source.external
                            else ""
                        )
                        raise ValueError(
                            f"attachment download returned HTTP {response.status}{detail}"
                        )
                    declared = response.content_length
                    if declared is not None and declared > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"attachment exceeds {MAX_DOWNLOAD_BYTES} bytes"
                        )
                    check_directory(directory, directory_fd)
                    temporary = f".max-mcp-{secrets.token_hex(16)}.part"
                    fd = os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    written = 0
                    head = b""
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            async for chunk in response.content.iter_chunked(
                                CHUNK_BYTES
                            ):
                                if len(head) < 16:
                                    head += chunk[: 16 - len(head)]
                                written += len(chunk)
                                if written > MAX_DOWNLOAD_BYTES:
                                    raise ValueError(
                                        f"attachment exceeds {MAX_DOWNLOAD_BYTES} bytes"
                                    )
                                stream.write(chunk)
                        if (
                            source.expected_size is not None
                            and written != source.expected_size
                        ):
                            raise ValueError(
                                "downloaded size does not match the attachment size"
                            )
                        filename = _with_extension(
                            safe_filename(source.filename, "download"),
                            kind,
                            response.headers.get("Content-Type"),
                            head,
                        )
                        target = publish_file(
                            temporary, directory, filename, directory_fd=directory_fd
                        )
                    finally:
                        os.unlink(temporary, dir_fd=directory_fd)
                    break
            else:
                raise ValueError("too many attachment redirects")
        # Response/session cleanup also awaits: validate after it, immediately
        # before returning the path. A move rolls back through the pinned fd.
        check_directory(directory, directory_fd)
        result = {"path": str(target), "size": written, "filename": target.name}
        target = None
        return result
    except (aiohttp.ClientError, TimeoutError):
        # CDN URLs may contain credentials. Never echo them through an HTTP error.
        raise ValueError("attachment download failed or timed out") from None
    finally:
        if target is not None:
            os.unlink(target.name, dir_fd=directory_fd)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def download_attachment(
        ctx: Context[ServerSession, AppCtx],
        chat_id: int,
        message_id: StrictInt | str,
        attach_index: int = 0,
        dest_dir: str | None = None,
    ) -> dict[str, Any]:
        """Скачать вложение сообщения MAX в разрешённый локальный каталог.

        Поддерживаются типы FILE, VIDEO, PHOTO и AUDIO; голосовые сообщения
        приходят как AUDIO. Индекс вложения берётся из поля attaches ответа
        read_messages. По умолчанию файл кладётся в первый разрешённый каталог
        (обычно ~/Downloads), список задаётся переменной MAX_MCP_DOWNLOAD_ROOTS.
        """
        message_id = parse_message_id(message_id)
        if (
            isinstance(attach_index, bool)
            or not isinstance(attach_index, int)
            or attach_index < 0
        ):
            raise ValueError("attach_index must be a non-negative integer")

        roots = _download_roots()
        if dest_dir is None and not roots:
            raise PermissionError(
                f"no allowed roots configured; set {DOWNLOAD_ROOTS_ENV}=/path1"
            )
        with open_directory(
            dest_dir if dest_dir is not None else str(roots[0]),
            roots,
            DOWNLOAD_ROOTS_ENV,
        ) as (directory, directory_fd):
            client = ctx.request_context.lifespan_context.client
            message = await client.get_message(chat_id, message_id)
            if message is None:
                raise ValueError(f"message {message_id} not found in chat {chat_id}")

            attaches = _field(message, "attaches") or []
            if attach_index >= len(attaches):
                raise ValueError(
                    f"message {message_id} has {len(attaches)} attachment(s), "
                    f"attach_index {attach_index} is out of range"
                )

            attach = attaches[attach_index]
            kind = enum_text(_field(attach, "type")) or "UNKNOWN"
            source = await _resolve_source(client, chat_id, message_id, attach, kind)
            result = await _fetch(
                source.url, directory, source, kind, directory_fd=directory_fd
            )

        result.update(
            {
                "chat_id": chat_id,
                "message_id": str(message_id),
                "attach_index": attach_index,
                "type": kind,
                "expected_size": source.expected_size,
                "size_match": (
                    None
                    if source.expected_size is None
                    else source.expected_size == result["size"]
                ),
            }
        )
        return result

    @mcp.tool()
    async def list_attachments(
        ctx: Context[ServerSession, AppCtx],
        chat_id: int,
        message_id: StrictInt | str,
    ) -> dict[str, Any]:
        """Показать вложения сообщения MAX с индексами для download_attachment."""
        message_id = parse_message_id(message_id)
        client = ctx.request_context.lifespan_context.client
        message = await client.get_message(chat_id, message_id)
        if message is None:
            raise ValueError(f"message {message_id} not found in chat {chat_id}")
        attaches = _field(message, "attaches") or []
        return {
            "chat_id": chat_id,
            "message_id": str(message_id),
            "attaches": [
                {**attach_to_dict(attach), "attach_index": index}
                for index, attach in enumerate(attaches)
            ],
        }
