import errno
import pathlib

import pytest

from max_mcp import client


def test_session_file_rejects_different_owner_by_default(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / client.SESSION_FILE
    session_path.write_text("session", encoding="utf-8")
    monkeypatch.setattr(client.os, "getuid", lambda: session_path.stat().st_uid + 1)

    with pytest.raises(RuntimeError, match="not owned by current user"):
        client._check_session_file(session_path)


def test_session_file_allows_synthetic_uid_with_explicit_flag(
    tmp_path, monkeypatch
) -> None:
    session_path = tmp_path / client.SESSION_FILE
    session_path.write_text("session", encoding="utf-8")
    session_path.chmod(0o600)
    monkeypatch.setattr(client.os, "getuid", lambda: session_path.stat().st_uid + 1)
    monkeypatch.setenv(client.ALLOW_SYNTHETIC_UID_ENV, "1")

    client._check_session_file(session_path)


def test_session_dir_allows_synthetic_uid_with_explicit_flag(
    tmp_path, monkeypatch
) -> None:
    tmp_path.chmod(0o700)
    monkeypatch.setattr(client, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(client.os, "getuid", lambda: tmp_path.stat().st_uid + 1)
    monkeypatch.setenv(client.ALLOW_SYNTHETIC_UID_ENV, "1")

    client._check_session_dir()


def test_synthetic_uid_flag_keeps_symlink_rejection(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / client.SESSION_FILE
    target_path = tmp_path / "target.db"
    target_path.write_text("session", encoding="utf-8")
    session_path.symlink_to(target_path)
    monkeypatch.setenv(client.ALLOW_SYNTHETIC_UID_ENV, "1")

    with pytest.raises(RuntimeError, match="symlink"):
        client._check_session_file(session_path)


def test_synthetic_uid_flag_keeps_regular_file_check(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / client.SESSION_FILE
    session_path.mkdir()
    monkeypatch.setenv(client.ALLOW_SYNTHETIC_UID_ENV, "1")

    with pytest.raises(RuntimeError, match="not a regular file"):
        client._check_session_file(session_path)


@pytest.mark.parametrize(
    "error_number",
    [
        errno.EPERM,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        errno.ENOTSUP,
    ],
)
def test_best_effort_chmod_suppresses_only_supported_errno(
    tmp_path, monkeypatch, error_number
) -> None:
    session_path = tmp_path / client.SESSION_FILE
    monkeypatch.setenv(client.BEST_EFFORT_CHMOD_ENV, "1")

    def fail_chmod(self: pathlib.Path, mode: int) -> None:
        raise OSError(error_number, "not supported")

    monkeypatch.setattr(pathlib.Path, "chmod", fail_chmod)

    client._chmod_path(session_path, 0o600)


def test_best_effort_chmod_keeps_other_permission_errors(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / client.SESSION_FILE
    monkeypatch.setenv(client.BEST_EFFORT_CHMOD_ENV, "1")

    def fail_chmod(self: pathlib.Path, mode: int) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(pathlib.Path, "chmod", fail_chmod)

    with pytest.raises(PermissionError):
        client._chmod_path(session_path, 0o600)
