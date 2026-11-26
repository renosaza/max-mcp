"""Общие проверки локальных путей для инструментов отправки и скачивания."""

import os
import pathlib
import stat
from collections.abc import Iterator
from contextlib import contextmanager

HOME = pathlib.Path.home().resolve()

DENY_ROOTS: tuple[pathlib.Path, ...] = (
    HOME / ".max-mcp",
    HOME / ".ssh",
    HOME / ".aws",
    HOME / ".gnupg",
    HOME / ".config",
    HOME / ".kube",
    HOME / ".docker",
    HOME / "Library" / "Keychains",
    pathlib.Path("/etc"),
    pathlib.Path("/private/etc"),
    pathlib.Path("/var"),
    pathlib.Path("/private/var"),
)


def is_within(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def allowed_roots(
    env_var: str,
    defaults: tuple[pathlib.Path, ...],
) -> tuple[pathlib.Path, ...]:
    """Читает белый список каталогов из переменной окружения либо берёт умолчания."""
    env = os.environ.get(env_var)
    if not env:
        return tuple(path for path in defaults if path.exists())
    roots: list[pathlib.Path] = []
    for raw in env.split(os.pathsep):
        raw = raw.strip()
        if raw:
            roots.append(pathlib.Path(raw).expanduser().absolute())
    return tuple(roots)


def reject_denied(resolved: pathlib.Path) -> None:
    for deny in DENY_ROOTS:
        try:
            deny_resolved = deny.resolve()
        except OSError:
            continue
        if is_within(resolved, deny_resolved):
            raise PermissionError(f"path is under denied root: {deny}")


def require_allowed(
    resolved: pathlib.Path,
    roots: tuple[pathlib.Path, ...],
    env_var: str,
) -> None:
    if not roots:
        raise PermissionError(
            f"no allowed roots configured; set {env_var}=/path1{os.pathsep}/path2"
        )
    if not any(is_within(resolved, root.resolve()) for root in roots):
        raise PermissionError(
            f"path is outside allowed roots: {[str(root) for root in roots]}"
        )


@contextmanager
def open_directory(
    dir_path: str,
    roots: tuple[pathlib.Path, ...],
    env_var: str,
) -> Iterator[tuple[pathlib.Path, int]]:
    """Проверяет каталог и удерживает его дескриптор на всё время скачивания."""
    if not isinstance(dir_path, str) or not dir_path:
        raise ValueError("directory must be a non-empty string")
    if "\x00" in dir_path:
        raise ValueError("directory contains NUL byte")

    path = pathlib.Path(dir_path).expanduser()
    if path.is_symlink():
        raise ValueError("symlinks are not allowed as a destination directory")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(f"directory does not resolve: {exc}") from exc
    if not stat.S_ISDIR(resolved.lstat().st_mode):
        raise ValueError("destination must be a directory")

    reject_denied(resolved)
    require_allowed(resolved, roots, env_var)

    # Pin each parent as well: O_NOFOLLOW on an absolute path checks only its
    # final component and would allow a parent replaced with a symlink.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(resolved.anchor, flags)
    try:
        for part in resolved.parts[1:]:
            child_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        check_directory(resolved, directory_fd)
        yield resolved, directory_fd
    finally:
        os.close(directory_fd)


def check_directory(directory: pathlib.Path, directory_fd: int) -> None:
    """Не возвращает путь, если каталог переместили или заменили после проверки."""
    try:
        unchanged = directory.resolve(strict=True) == directory and os.path.samestat(
            directory.lstat(), os.fstat(directory_fd)
        )
    except (OSError, RuntimeError):
        unchanged = False
    if not unchanged:
        raise ValueError(
            "destination directory changed during download; retry with its current path"
        )


def safe_filename(name: str, fallback: str) -> str:
    """Делает из присланного отправителем имени безопасное имя файла.

    Имя вложения приходит с чужой стороны, поэтому от него берётся только
    последний сегмент, а разделители пути и управляющие символы вычищаются.
    """
    candidate = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(
        char for char in candidate if char.isprintable() and char not in '\x00:*?"<>|'
    ).strip(". ")
    if not candidate or candidate in {".", ".."}:
        return fallback
    return candidate.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(". ")


def publish_file(
    temporary: str, directory: pathlib.Path, filename: str, *, directory_fd: int
) -> pathlib.Path:
    """Атомарно публикует готовый файл, не заменяя файлы или симлинки."""
    check_directory(directory, directory_fd)
    name = pathlib.Path(filename)
    for index in range(1000):
        target = directory / (
            filename if index == 0 else f"{name.stem}-{index}{name.suffix}"
        )
        try:
            os.link(
                temporary,
                target.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            continue
        return target
    raise RuntimeError(f"cannot find a free filename for {filename} in {directory}")
