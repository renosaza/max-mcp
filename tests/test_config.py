import asyncio
from types import SimpleNamespace

from max_mcp import auth, client
from max_mcp.config import DEFAULT_APP_VERSION, mobile_client_options


def test_latest_max_version_uses_remote_fingerprint(monkeypatch):
    monkeypatch.delenv("MAX_MCP_APP_VERSION", raising=False)
    options = mobile_client_options()
    assert options["app_version"] == DEFAULT_APP_VERSION == "26.35.1"
    assert options["catalog"].remote is True


def test_bundled_version_needs_no_network(monkeypatch):
    monkeypatch.setenv("MAX_MCP_APP_VERSION", "26.28.0")
    options = mobile_client_options()
    assert options["app_version"] == "26.28.0"
    assert options["catalog"].remote is False


def test_all_client_paths_disable_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "SESSION_DIR", tmp_path)
    created = []

    def make_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(client, "WebClient", make_client)
    monkeypatch.setattr(client, "Client", make_client)

    client._build_client()  # QR/web session
    (tmp_path / client.KIND_FILE).write_text("sms")
    (tmp_path / client.PHONE_FILE).write_text("+79991234567")
    client._build_client()  # SMS/mobile session

    assert len(created) == 2
    assert all(
        x["extra_config"].log_level == "CRITICAL"
        and x["extra_config"].telemetry is False
        for x in created
    )
    assert created[1]["app_version"] == DEFAULT_APP_VERSION


def test_auth_paths_share_secure_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path)
    created = []

    def make_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace()

    async def fake_finish_login(_client, _kind, phone=None):
        return None

    monkeypatch.setattr(auth, "WebClient", make_client)
    monkeypatch.setattr(auth, "Client", make_client)
    monkeypatch.setattr(auth, "_finish_login", fake_finish_login)

    asyncio.run(auth._login_qr())
    asyncio.run(auth._login_sms("+79991234567"))

    assert all(x["extra_config"].telemetry is False for x in created)
    assert all(x["extra_config"].log_level == "CRITICAL" for x in created)
    assert created[1]["app_version"] == DEFAULT_APP_VERSION
