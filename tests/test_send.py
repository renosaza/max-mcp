import asyncio
from types import SimpleNamespace

import pytest

from max_mcp.tools import _paths, send


def test_shared_path_policy_preserves_send_restrictions(tmp_path, monkeypatch):
    allowed = tmp_path / "outbox"
    allowed.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    report = allowed / "report.pdf"
    report.write_bytes(b"%PDF-1.4")
    protected = secret / "session.db"
    protected.write_bytes(b"test fixture, not a real session")
    link = allowed / "linked.pdf"
    link.symlink_to(report)
    monkeypatch.setattr(_paths, "DENY_ROOTS", (secret,))
    monkeypatch.setenv(send.SEND_ROOTS_ENV, str(allowed))

    assert send._validate_path(str(report)) == report.resolve()
    with pytest.raises(ValueError, match="symlinks"):
        send._validate_path(str(link))
    with pytest.raises(ValueError, match="regular file"):
        send._validate_path(str(allowed))
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    with pytest.raises(PermissionError, match="outside allowed roots"):
        send._validate_path(str(outside))
    monkeypatch.setenv(send.SEND_ROOTS_ENV, str(tmp_path))
    with pytest.raises(PermissionError, match="denied root"):
        send._validate_path(str(protected))
    monkeypatch.setattr(send, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="exceeds"):
        send._validate_path(str(report))


def test_reply_id_string_is_preserved_and_invalid_ids_never_send():
    tools = {}

    class FakeMCP:
        def tool(self):
            def register(function):
                tools[function.__name__] = function
                return function
            return register

    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        return {"id": 1, "text": kwargs["text"]}

    client = SimpleNamespace(send_message=send_message)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )
    send.register(FakeMCP())
    reply_id = "116691985249148381"
    asyncio.run(tools["send_message"](ctx, chat_id=2, text="reply", reply_to_id=reply_id))
    assert calls == [{"chat_id": 2, "text": "reply", "reply_to": int(reply_id)}]
    for value in (True, 1.5, "1e17"):
        with pytest.raises(ValueError, match="message id"):
            asyncio.run(tools["send_message"](ctx, chat_id=2, text="reply", reply_to_id=value))
    assert len(calls) == 1
