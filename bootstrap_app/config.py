from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class BootstrapConfigurationError(RuntimeError):
    """The one-time bootstrap process was started with unsafe configuration."""


def _absolute_path(raw: str, name: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise BootstrapConfigurationError(f"{name} must be an absolute path")
    # Keep the lexical path so later lstat checks can reject a symlink rather
    # than silently resolving it to a different host location.
    return Path(os.path.abspath(path))


def _assert_private_regular_file(path: Path, name: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BootstrapConfigurationError(f"cannot read {name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BootstrapConfigurationError(f"{name} must be a regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise BootstrapConfigurationError(f"{name} permissions must be 0600 or stricter")


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    deploy_root: Path
    host_deploy_root: Path
    token_file: Path
    control_dir: Path
    installation_dir: Path
    token: bytes
    request_max_bytes: int = 2 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "BootstrapSettings":
        deploy_root = _absolute_path(
            os.getenv("BOOTSTRAP_DEPLOY_ROOT", "/bootstrap-data"),
            "BOOTSTRAP_DEPLOY_ROOT",
        )
        host_deploy_root = _absolute_path(
            os.getenv("BOOTSTRAP_HOST_DEPLOY_ROOT", "/srv/wemini-bootstrap"),
            "BOOTSTRAP_HOST_DEPLOY_ROOT",
        )
        token_file = _absolute_path(
            os.getenv(
                "BOOTSTRAP_TOKEN_FILE",
                str(deploy_root / "control" / "bootstrap_token"),
            ),
            "BOOTSTRAP_TOKEN_FILE",
        )
        try:
            token_file.relative_to(deploy_root)
        except ValueError as exc:
            raise BootstrapConfigurationError(
                "BOOTSTRAP_TOKEN_FILE must be inside BOOTSTRAP_DEPLOY_ROOT"
            ) from exc

        _assert_private_regular_file(token_file, "BOOTSTRAP_TOKEN_FILE")
        try:
            token = token_file.read_bytes().strip()
        except OSError as exc:
            raise BootstrapConfigurationError("cannot read BOOTSTRAP_TOKEN_FILE") from exc
        if len(token) < 32:
            raise BootstrapConfigurationError(
                "BOOTSTRAP_TOKEN_FILE must contain at least 32 bytes"
            )

        raw_max_bytes = os.getenv("BOOTSTRAP_REQUEST_MAX_BYTES", "2097152")
        try:
            request_max_bytes = int(raw_max_bytes)
        except ValueError as exc:
            raise BootstrapConfigurationError(
                "BOOTSTRAP_REQUEST_MAX_BYTES must be an integer"
            ) from exc
        if not 64 * 1024 <= request_max_bytes <= 10 * 1024 * 1024:
            raise BootstrapConfigurationError(
                "BOOTSTRAP_REQUEST_MAX_BYTES must be between 64 KiB and 10 MiB"
            )

        return cls(
            deploy_root=deploy_root,
            host_deploy_root=host_deploy_root,
            token_file=token_file,
            control_dir=deploy_root / "control",
            installation_dir=deploy_root / "installation",
            token=token,
            request_max_bytes=request_max_bytes,
        )
