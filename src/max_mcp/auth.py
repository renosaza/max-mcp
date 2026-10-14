import argparse
import asyncio
import os
import pathlib
import secrets
import stat
import sys
from typing import Any

from pymax import (
    Client,
    ConsolePasswordProvider,
    ConsoleQrHandler,
    ConsoleSmsCodeProvider,
    WebClient,
)

from .config import mobile_client_options, secure_client_config

SESSION_DIR = pathlib.Path.home() / ".max-mcp"
SESSION_FILE = "session.db"
KIND_FILE = "session.kind"
PHONE_FILE = "session.phone"


def _ensure_session_dir() -> None:
    SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
    st = SESSION_DIR.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"{SESSION_DIR} is a symlink; refusing to use")
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"{SESSION_DIR} is not a directory")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{SESSION_DIR} is not owned by current user")
    if st.st_mode & 0o077:
        SESSION_DIR.chmod(0o700)


def _write_secret(path: pathlib.Path, data: str) -> None:
    """Atomically write a small secret file with mode 0600."""
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(tmp_path), flags, 0o600)
    published = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        published = True
        path.chmod(0o600)
    finally:
        if not published:
            tmp_path.unlink(missing_ok=True)


def _harden_session_file() -> None:
    path = SESSION_DIR / SESSION_FILE
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"{path} is a symlink; refusing to chmod target")
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"{path} is not a regular file")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{path} is not owned by current user")
    path.chmod(0o600)


def _mark_session(kind: str, phone: str | None = None) -> None:
    _write_secret(SESSION_DIR / KIND_FILE, kind)
    if phone:
        _write_secret(SESSION_DIR / PHONE_FILE, phone)
    else:
        (SESSION_DIR / PHONE_FILE).unlink(missing_ok=True)


def _print_me(client: Any) -> None:
    me = getattr(client.me, "contact", client.me)
    print(
        f"Вход выполнен: {getattr(me, 'first_name', '?')} "
        f"(id={getattr(me, 'id', '?')})",
        file=sys.stderr,
    )


async def _finish_login(client: Any, kind: str, phone: str | None = None) -> None:
    """Run a PyMax login and treat post-login cancellation as normal shutdown."""
    logged_in = asyncio.Event()

    @client.on_start()
    async def _ready(current_client: Any) -> None:
        _print_me(current_client)
        logged_in.set()
        await current_client.stop()

    try:
        await client.start()
    except asyncio.CancelledError:
        # PyMax may cancel its WebSocket reader when stop() is called from on_start.
        # That cancellation is expected only after the callback confirmed login.
        if not logged_in.is_set():
            raise

    if not logged_in.is_set():
        raise RuntimeError("Клиент MAX завершился до подтверждения входа")

    _harden_session_file()
    _mark_session(kind, phone=phone)


async def _login_qr() -> None:
    _ensure_session_dir()
    client = WebClient(
        extra_config=secure_client_config(),
        work_dir=str(SESSION_DIR),
        session_name=SESSION_FILE,
        qr_provider=ConsoleQrHandler(),
    )
    await _finish_login(client, "web")


async def _login_sms(phone: str) -> None:
    _ensure_session_dir()
    client = Client(
        phone=phone,
        extra_config=secure_client_config(),
        **mobile_client_options(),
        work_dir=str(SESSION_DIR),
        session_name=SESSION_FILE,
        sms_code_provider=ConsoleSmsCodeProvider(),
        password_provider=ConsolePasswordProvider(),
    )
    await _finish_login(client, "sms", phone=phone)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="max-mcp-login",
        description="Авторизация пользовательской сессии MAX для max-mcp",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login-qr", help="Войти по QR-коду через мобильное приложение MAX")
    sms = sub.add_parser("login-sms", help="Войти по номеру телефона и SMS-коду")
    sms.add_argument(
        "--phone",
        required=True,
        help="Номер в формате E.164, например +79991234567",
    )
    sub.add_parser("login", help="Устаревший псевдоним login-qr")
    args = parser.parse_args()

    if args.cmd in ("login-qr", "login"):
        asyncio.run(_login_qr())
    elif args.cmd == "login-sms":
        asyncio.run(_login_sms(args.phone))


if __name__ == "__main__":
    main()
