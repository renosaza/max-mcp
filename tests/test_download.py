import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from max_mcp.tools import _paths, download
from max_mcp.tools._paths import safe_filename, unique_path

PAYLOAD = b"max attachment payload" * 16


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
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args) -> None:
        return None


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class FakeClient:
    def __init__(self, attaches, *, file_url=None, video_url=None, external=None) -> None:
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
    attach = SimpleNamespace(type="AUDIO", audio_id=9, url=f"{server}/typed", duration=12)
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


def test_externally_hosted_video_reports_its_source(server, tmp_path, monkeypatch) -> None:
    # видео-репосты живут на инфраструктуре Одноклассников: MAX отдаёт ссылку,
    # а CDN на неё отвечает отказом
    attach = SimpleNamespace(type="VIDEO", video_id=3, token="t")
    client = FakeClient(
        [attach],
        video_url=f"{server}/refused",
        external="https://m.ok.ru/video/9826404491253",
    )
    run = tool("download_attachment", tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="hosted outside MAX"):
        asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))


def test_sender_filename_cannot_escape_destination(server, tmp_path, monkeypatch) -> None:
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
            SimpleNamespace(type="PHOTO", photo_id=1, base_url="u"),
            SimpleNamespace(type="FILE", file_id=2, name="a.pdf", size=3),
        ]
    )
    run = tool("list_attachments", tmp_path, monkeypatch)

    result = asyncio.run(run(make_ctx(client), chat_id=1, message_id=7))

    assert [item["attach_index"] for item in result["attaches"]] == [0, 1]
    assert result["attaches"][1]["name"] == "a.pdf"


def test_safe_filename_falls_back_on_empty_input() -> None:
    assert safe_filename("", "fallback.bin") == "fallback.bin"
    assert safe_filename("..", "fallback.bin") == "fallback.bin"
    assert safe_filename("a/b/c.txt", "fallback.bin") == "c.txt"


def test_unique_path_appends_suffix(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert unique_path(tmp_path, "a.txt").name == "a-1.txt"
