import asyncio
from types import SimpleNamespace

import pytest

from max_mcp import auth


class SuccessfulCancelledClient:
    def __init__(self) -> None:
        self.callback = None
        self.me = SimpleNamespace(first_name="Test", id=123)

    def on_start(self):
        def decorator(callback):
            self.callback = callback
            return callback

        return decorator

    async def start(self) -> None:
        assert self.callback is not None
        await self.callback(self)
        raise asyncio.CancelledError

    async def stop(self) -> None:
        return None


class EarlyCancelledClient(SuccessfulCancelledClient):
    async def start(self) -> None:
        raise asyncio.CancelledError


def test_successful_login_tolerates_post_login_cancellation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    (tmp_path / auth.SESSION_FILE).write_text("session", encoding="utf-8")

    asyncio.run(auth._finish_login(SuccessfulCancelledClient(), "web"))

    assert (tmp_path / auth.KIND_FILE).read_text(encoding="utf-8") == "web"
    assert (tmp_path / auth.SESSION_FILE).stat().st_mode & 0o777 == 0o600


def test_cancellation_before_login_is_not_hidden(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    (tmp_path / auth.SESSION_FILE).write_text("session", encoding="utf-8")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(auth._finish_login(EarlyCancelledClient(), "web"))



def test_profile_contact_is_printed_after_new_pymax_login(capsys):
    client = SimpleNamespace(
        me=SimpleNamespace(contact=SimpleNamespace(first_name="Нина", id=345))
    )

    auth._print_me(client)

    output = capsys.readouterr()
    assert "Нина" in output.err
    assert "id=345" in output.err
