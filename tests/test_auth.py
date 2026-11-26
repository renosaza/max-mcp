import asyncio
from types import SimpleNamespace

import pytest

from max_mcp import auth


class LoginClient:
    def __init__(self) -> None:
        self.me = None
        self.stopped = False

    async def connect(self) -> None:
        self.me = SimpleNamespace(contact=SimpleNamespace(first_name="Test", id=123))

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.parametrize("kind,phone", [("web", None), ("sms", "+79991234567")])
def test_login_finishes_without_start_loop(tmp_path, monkeypatch, kind, phone) -> None:
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    (tmp_path / auth.SESSION_FILE).write_text("session", encoding="utf-8")
    (tmp_path / auth.PHONE_FILE).write_text("old phone", encoding="utf-8")
    client = LoginClient()

    asyncio.run(auth._finish_login(client, kind, phone=phone))

    assert client.stopped
    assert (tmp_path / auth.KIND_FILE).read_text(encoding="utf-8") == kind
    assert (tmp_path / auth.SESSION_FILE).stat().st_mode & 0o777 == 0o600
    assert (tmp_path / auth.KIND_FILE).stat().st_mode & 0o777 == 0o600
    if phone:
        assert (tmp_path / auth.PHONE_FILE).read_text(encoding="utf-8") == phone
        assert (tmp_path / auth.PHONE_FILE).stat().st_mode & 0o777 == 0o600
    else:
        assert not (tmp_path / auth.PHONE_FILE).exists()


@pytest.mark.parametrize(
    "failure", [RuntimeError("login failed"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("stop_fails", [False, True])
def test_failed_login_stops_client_without_publishing_session(
    tmp_path, monkeypatch, failure, stop_fails
):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    (tmp_path / auth.SESSION_FILE).write_text("session", encoding="utf-8")

    class FailedClient(LoginClient):
        async def connect(self):
            raise failure

        async def stop(self):
            self.stopped = True
            if stop_fails:
                raise RuntimeError("shutdown failed")

    client = FailedClient()
    with pytest.raises(type(failure)) as caught:
        asyncio.run(auth._finish_login(client, "web"))

    assert caught.value is failure
    assert client.stopped
    assert not (tmp_path / auth.KIND_FILE).exists()


def test_login_requires_confirmed_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)

    class UnconfirmedClient(LoginClient):
        async def connect(self):
            return None

    client = UnconfirmedClient()
    with pytest.raises(RuntimeError, match="до подтверждения входа"):
        asyncio.run(auth._finish_login(client, "web"))

    assert client.stopped
    assert not (tmp_path / auth.KIND_FILE).exists()


def test_external_cancellation_stops_client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)

    async def exercise():
        connecting = asyncio.Event()

        class WaitingClient(LoginClient):
            async def connect(self):
                connecting.set()
                await asyncio.Event().wait()

        client = WaitingClient()
        task = asyncio.create_task(auth._finish_login(client, "web"))
        await connecting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.stopped

    asyncio.run(exercise())
    assert not (tmp_path / auth.KIND_FILE).exists()


def test_session_is_hardened_even_if_shutdown_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    (tmp_path / auth.SESSION_FILE).write_text("session", encoding="utf-8")

    class FailedStopClient(LoginClient):
        async def stop(self):
            raise RuntimeError("shutdown failed")

    with pytest.raises(RuntimeError, match="shutdown failed"):
        asyncio.run(auth._finish_login(FailedStopClient(), "web"))

    assert (tmp_path / auth.SESSION_FILE).stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / auth.KIND_FILE).exists()


def test_profile_contact_is_printed_after_new_pymax_login(capsys):
    client = SimpleNamespace(
        me=SimpleNamespace(contact=SimpleNamespace(first_name="Нина", id=345))
    )

    auth._print_me(client)

    output = capsys.readouterr()
    assert "Нина" in output.err
    assert "id=345" in output.err
