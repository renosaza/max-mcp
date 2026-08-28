"""Общие проверки локальных путей для инструментов отправки и скачивания."""

import os
import pathlib
import stat

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
        return tuple(path.resolve() for path in defaults if path.exists())
    roots: list[pathlib.Path] = []
    for raw in env.split(os.pathsep):
        raw = raw.strip()
        if raw:
            roots.append(pathlib.Path(raw).expanduser().resolve())
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
    if not any(is_within(resolved, root) for root in roots):
        raise PermissionError(
            f"path is outside allowed roots: {[str(root) for root in roots]}"
        )


def validate_directory(
    dir_path: str,
    roots: tuple[pathlib.Path, ...],
    env_var: str,
) -> pathlib.Path:
    """Проверяет каталог назначения: существует, не симлинк, внутри белого списка."""
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
    return resolved


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
    return candidate[:200]


def unique_path(directory: pathlib.Path, filename: str) -> pathlib.Path:
    """Подбирает свободное имя, не затирая уже лежащий рядом файл."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find a free filename for {filename} in {directory}")
