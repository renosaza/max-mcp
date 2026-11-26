import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from max_mcp.tools import _paths, download
from max_mcp.tools._paths import safe_filename

PAYLOAD = b"max attachment payload" * 16


@pytest.fixture(autouse=True)
def allow_pytest_temp_root(tmp_path, monkeypatch):
    # macOS puts pytest's temporary directories under the otherwise denied /var.
    # Explicit denial tests install their own deny roots after this fixture.
    monkeypatch.setattr(
        _paths,
        "DENY_ROOTS",
        tuple(
            root
            for root in _paths.DENY_ROOTS
            if not _paths.is_within(tmp_path.resolve(), root.resolve())
        ),
    )


async def fetch(url, directory, source, kind):
    with _paths.open_directory(
        str(directory), (directory,), download.DOWNLOAD_ROOTS_ENV
    ) as (checked, directory_fd):
        return await download._fetch(
            url, checked, source, kind, directory_fd=directory_fd
        )


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/redirect", "/unsafe-redirect", "/loop"}:
            self.send_response(302)
            self.send_header(
                "Location",
                {
                    "/redirect": "/file",
                    "/unsafe-redirect": "https://127.0.0.1/private",
                    "/loop": "/loop",
                }[self.path],
            )
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/refused":
            self.send_response(400)
            self.end_headers()
            return
        typed = self.path.startswith("/typed")
        content_type = "audio/mp4" if typed else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if self.path != "/stream":
            length = len(PAYLOAD) + (1 if self.path == "/truncated" else 0)
            self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args) -> None:
        return None


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    validate_url = download._validate_url

    def allow_test_server(value):
        if not value.startswith(url + "/"):
            validate_url(value)

    # Only this fixture's test server is exempt from the production CDN policy.
    monkeypatch.setattr(download, "_validate_url", allow_test_server)
    try:
        yield url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class FakeClient:
    def __init__(
        self, attaches, *, file_url=None, video_url=None, external=None
    ) -> None:
        self.message = SimpleNamespace(id=7, chat_id=1, attaches=attaches)
        self.file_url = file_url
        self.video_url = video_url
        self.external = external
        self.calls = []

    async def get_message(self, chat_id, message_id):
        self.calls.append(("get_message", chat_id, message_id))
        return self.message

    async def get_file_by_id(self, chat_id, message_id, file_id):
        self.calls.append(("get_file_by_id", chat_id, message_id, file_id))
        return SimpleNamespace(url=self.file_url, unsafe=False)

    async def get_video_by_id(self, chat_id, message_id, video_id):
        self.calls.append(("get_video_by_id", chat_id, message_id, video_id))
        return SimpleNamespace(url=self.video_url, external=self.external, cache=False)


def make_ctx(client):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )


def tool(name, roots, monkeypatch):
    monkeypatch.setenv(download.DOWNLOAD_ROOTS_ENV, str(roots))
    mcp = FakeMCP()
    download.register(mcp)
    return mcp.tools[name]


def test_file_download_uses_get_file_by_id(server, tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(
        type="FILE", file_id=42, name="отчёт.pdf", size=len(PAYLOAD), token="t"
    )
    client = FakeClient([attach], file_url=f"{server}/file")
    run = tool("download_attachment", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert ("get_file_by_id", 1, 7, 42) in client.calls
    assert result["filename"] == "отчёт.pdf"
    assert result["size"] == len(PAYLOAD)
    assert result["size_match"] is True
    assert (tmp_path / "отчёт.pdf").read_bytes() == PAYLOAD


def test_photo_download_uses_base_url(server, tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(type="PHOTO", photo_id=5, base_url=f"{server}/photo")
    client = FakeClient([attach])
    run = tool("download_attachment", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert result["type"] == "PHOTO"
    assert result["filename"] == "photo_5.jpg"
    assert result["size_match"] is None
    assert (tmp_path / "photo_5.jpg").read_bytes() == PAYLOAD


def test_audio_download_uses_url(server, tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(
        type="AUDIO", audio_id=9, url=f"{server}/typed", duration=12
    )
    client = FakeClient([attach])
    run = tool("download_attachment", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert result["filename"] == "audio_9.m4a"
    assert (tmp_path / "audio_9.m4a").read_bytes() == PAYLOAD


def test_video_download_uses_get_video_by_id(server, tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(type="VIDEO", video_id=3, token="t")
    client = FakeClient([attach], video_url=f"{server}/video")
    run = tool("download_attachment", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert ("get_video_by_id", 1, 7, 3) in client.calls
    assert result["filename"] == "video_3.mp4"


def test_external_video_without_url_is_reported(tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(type="VIDEO", video_id=3, token="t")
    client = FakeClient([attach], video_url=None)
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="direct url"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))


def test_externally_hosted_video_reports_its_source(
    server, tmp_path, monkeypatch
) -> None:
    # видео-репосты живут на инфраструктуре Одноклассников: MAX отдаёт ссылку,
    # а CDN на неё отвечает отказом
    attach = SimpleNamespace(type="VIDEO", video_id=3, token="t")
    client = FakeClient(
        [attach],
        video_url=f"{server}/refused",
        external="https://m.ok.ru/video/9826404491253",
    )
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="external video") as error:
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))
    assert client.external not in str(error.value)


def test_sender_filename_cannot_escape_destination(
    server, tmp_path, monkeypatch
) -> None:
    attach = SimpleNamespace(
        type="FILE", file_id=1, name="../../etc/passwd", size=len(PAYLOAD)
    )
    client = FakeClient([attach], file_url=f"{server}/file")
    run = tool("download_attachment", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert result["filename"] == "passwd"
    assert (tmp_path / "passwd").exists()


def test_second_download_does_not_overwrite(server, tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(type="FILE", file_id=1, name="note.txt", size=len(PAYLOAD))
    client = FakeClient([attach], file_url=f"{server}/file")
    run = tool("download_attachment", tmp_path, monkeypatch)

    first = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))
    second = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert first["filename"] == "note.txt"
    assert second["filename"] == "note-1.txt"


def test_dest_dir_outside_allowed_roots_is_refused(tmp_path, monkeypatch) -> None:
    # временный каталог pytest на macOS лежит под /private/var, который сам по
    # себе в запретных корнях; здесь проверяется именно белый список
    monkeypatch.setattr(_paths, "DENY_ROOTS", ())
    attach = SimpleNamespace(type="FILE", file_id=1, name="note.txt", size=1)
    client = FakeClient([attach], file_url="http://127.0.0.1:1/file")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    run = tool("download_attachment", allowed, monkeypatch)

    with pytest.raises(PermissionError, match="outside allowed roots"):
        asyncio.run(
            run(make_ctx(client), chat_id=1, message_id=7, dest_dir=str(outside))
        )


def test_denied_dest_dir_is_refused(tmp_path, monkeypatch) -> None:
    attach = SimpleNamespace(type="FILE", file_id=1, name="note.txt", size=1)
    client = FakeClient([attach], file_url="http://127.0.0.1:1/file")
    denied = tmp_path / "secrets"
    denied.mkdir()
    monkeypatch.setattr(_paths, "DENY_ROOTS", (denied,))
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(PermissionError, match="denied root"):
        asyncio.run(
            run(make_ctx(client), chat_id=1, message_id=7, dest_dir=str(denied))
        )


def test_attach_index_out_of_range(tmp_path, monkeypatch) -> None:
    client = FakeClient([])
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7, attach_index=0))


def test_unsupported_attachment_type(tmp_path, monkeypatch) -> None:
    client = FakeClient([SimpleNamespace(type="CONTACT", name="Иван")])
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="no downloadable payload"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))


def test_list_attachments_numbers_attachments(tmp_path, monkeypatch) -> None:
    client = FakeClient(
        [
            SimpleNamespace(type="PHOTO", photo_id=1, base_url="u", attach_index=999),
            SimpleNamespace(type="FILE", file_id=2, name="a.pdf", size=3),
        ]
    )
    run = tool("list_attachments", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert [item["attach_index"] for item in result["attaches"]] == [0, 1]
    assert result["attaches"][1]["name"] == "a.pdf"


def test_decimal_message_id_reaches_sdk_without_rounding(tmp_path, monkeypatch):
    message_id = "116691985249148381"
    client = FakeClient([])
    run = tool("list_attachments", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=message_id))

    assert client.calls == [("get_message", 1, int(message_id))]
    assert result["message_id"] == message_id


@pytest.mark.parametrize(
    "message_id", [True, 1.5, "1.5", "1e16", "--1", "", " 1", "１２"]
)
def test_invalid_message_id_never_calls_sdk(tmp_path, monkeypatch, message_id):
    client = FakeClient([])
    run = tool("list_attachments", tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="message id"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=message_id))
    assert client.calls == []


@pytest.mark.parametrize("name", ["download_attachment", "list_attachments"])
@pytest.mark.parametrize("message_id", [True, 1.0])
def test_mcp_validation_does_not_coerce_message_ids(name, message_id, monkeypatch):
    client = FakeClient([])
    mcp = FastMCP("test-download-validation")
    download.register(mcp)
    monkeypatch.setattr(mcp, "get_context", lambda: make_ctx(client))

    with pytest.raises(ToolError, match="message_id"):
        asyncio.run(mcp.call_tool(name, {"chat_id": 1, "message_id": message_id}))

    assert client.calls == []


def test_safe_filename_falls_back_on_empty_input() -> None:
    assert safe_filename("", "fallback.bin") == "fallback.bin"
    assert safe_filename("..", "fallback.bin") == "fallback.bin"
    assert safe_filename("a/b/c.txt", "fallback.bin") == "c.txt"


def test_default_destination_is_validated(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_paths, "DENY_ROOTS", (tmp_path,))
    client = FakeClient([])
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(PermissionError, match="denied root"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))
    assert client.calls == []


def test_configured_symlink_destination_is_refused(tmp_path, monkeypatch) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    client = FakeClient([])
    run = tool("download_attachment", link, monkeypatch)

    with pytest.raises(ValueError, match="symlinks"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))
    assert client.calls == []


def test_concurrent_downloads_preserve_existing_files_and_symlinks(
    server, tmp_path, monkeypatch
):
    attach = SimpleNamespace(type="FILE", file_id=1, name="note.txt", size=len(PAYLOAD))
    client = FakeClient([attach], file_url=f"{server}/file")
    run = tool("download_attachment", tmp_path, monkeypatch)
    missing_target = tmp_path / "never-create"
    (tmp_path / "note.txt").symlink_to(missing_target)
    (tmp_path / "note-1.txt").write_text("keep")

    async def concurrent():
        return await asyncio.gather(
            *(run(make_ctx(client), chat_id=1, message_id=7) for _ in range(3))
        )

    results = asyncio.run(concurrent())

    assert {item["filename"] for item in results} == {
        "note-2.txt",
        "note-3.txt",
        "note-4.txt",
    }
    assert (tmp_path / "note.txt").is_symlink()
    assert not missing_target.exists()
    assert (tmp_path / "note-1.txt").read_text() == "keep"
    for item in results:
        path = tmp_path / item["filename"]
        assert path.read_bytes() == PAYLOAD
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("replace_parent", [False, True])
@pytest.mark.parametrize("when", ["sdk", "publish", "linked"])
def test_directory_replacement_cannot_redirect_writes(
    server, tmp_path, monkeypatch, replace_parent, when
):
    allowed = tmp_path / "allowed"
    directory = allowed / "downloads"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if replace_parent:
        (outside / "downloads").mkdir()
    (outside / "sentinel").write_text("keep")
    replaced = allowed if replace_parent else directory
    moved = tmp_path / "moved"

    def replace_directory():
        replaced.rename(moved)
        replaced.symlink_to(outside, target_is_directory=True)

    attach = SimpleNamespace(type="FILE", file_id=1, name="note.txt", size=len(PAYLOAD))
    client = FakeClient([attach], file_url=f"{server}/file")
    run = tool("download_attachment", directory, monkeypatch)
    if when == "sdk":
        get_message = client.get_message

        async def changed_message(*args):
            message = await get_message(*args)
            replace_directory()
            return message

        monkeypatch.setattr(client, "get_message", changed_message)
    elif when == "publish":
        publish = download.publish_file

        def changed_publish(*args, **kwargs):
            replace_directory()
            return publish(*args, **kwargs)

        monkeypatch.setattr(download, "publish_file", changed_publish)
    else:
        link = os.link

        def changed_link(*args, **kwargs):
            result = link(*args, **kwargs)
            replace_directory()
            return result

        monkeypatch.setattr(os, "link", changed_link)

    with pytest.raises(ValueError, match="directory changed"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert (outside / "sentinel").read_text() == "keep"
    assert [path.name for path in outside.rglob("*") if path.is_file()] == ["sentinel"]
    assert not any(path.is_file() or path.is_symlink() for path in moved.rglob("*"))


@pytest.mark.parametrize("endpoint", ["file", "stream"])
def test_size_limit_removes_partial_download(server, tmp_path, monkeypatch, endpoint):
    monkeypatch.setattr(download, "MAX_DOWNLOAD_BYTES", 10)
    source = download._Source(f"{server}/{endpoint}", "note.txt", None)

    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(fetch(source.url, tmp_path, source, "FILE"))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "endpoint,size", [("file", len(PAYLOAD) + 1), ("truncated", None)]
)
def test_incomplete_download_is_not_published(server, tmp_path, endpoint, size):
    source = download._Source(f"{server}/{endpoint}", "note.txt", size)

    with pytest.raises(ValueError):
        asyncio.run(fetch(source.url, tmp_path, source, "FILE"))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "url",
    [
        "http://max.ru/file",
        "https://max.ru.evil.example/file",
        "https://evilmax.ru/file",
        "https://localhost/file",
        "https://127.0.0.1/file",
        "https://[::1]/file",
        "https://user:password@max.ru/file",
        "https://max.ru:8443/file",
        "file:///etc/passwd",
    ],
)
def test_untrusted_download_urls_are_refused(url):
    with pytest.raises(ValueError, match="approved MAX CDN"):
        download._validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://max.ru/file",
        "https://cdn.oneme.ru/file",
        "https://st.okcdn.ru/file",
        "https://vd.mycdn.me/file",
    ],
)
def test_max_cdn_urls_are_allowed(url):
    download._validate_url(url)


def test_redirects_are_checked_and_bounded(server, tmp_path):
    source = download._Source(f"{server}/redirect", "note.txt", None)
    result = asyncio.run(fetch(source.url, tmp_path, source, "FILE"))
    assert (tmp_path / result["filename"]).read_bytes() == PAYLOAD
    for endpoint, error in [
        ("unsafe-redirect", "approved MAX CDN"),
        ("loop", "too many"),
    ]:
        with pytest.raises(ValueError, match=error):
            asyncio.run(fetch(f"{server}/{endpoint}", tmp_path, source, "FILE"))
    assert [path.name for path in tmp_path.iterdir()] == ["note.txt"]


def test_long_unicode_filename_fits_filesystem():
    filename = safe_filename("я" * 300, "fallback")
    assert 0 < len(filename.encode("utf-8")) <= 200


@pytest.mark.parametrize(
    "head,kind,expected",
    [
        (b"RIFF1234WEBP", "PHOTO", ".webp"),
        (b"OggS", "AUDIO", ".ogg"),
        (b"\x89PNG\r\n\x1a\n", "PHOTO", ".png"),
        (b"0000ftypM4A ", "AUDIO", ".m4a"),
    ],
)
def test_media_signature_takes_precedence_over_declared_type(head, kind, expected):
    assert download._extension_for(kind, "application/octet-stream", head) == expected
