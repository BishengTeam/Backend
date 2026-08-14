from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterator


class BootstrapStateError(RuntimeError):
    """The persisted bootstrap state is missing, corrupt, or invalid."""


class BootstrapCompletedError(BootstrapStateError):
    """The one-time bootstrap endpoint has already been permanently closed."""


class BootstrapPhase(StrEnum):
    NEW = "NEW"
    CONFIGURED = "CONFIGURED"
    QUALITY_RUNNING = "QUALITY_RUNNING"
    QUALITY_PASSED = "QUALITY_PASSED"
    INFRA_READY = "INFRA_READY"
    MIGRATED = "MIGRATED"
    AWAITING_ADMIN = "AWAITING_ADMIN"
    ADMIN_CREATED = "ADMIN_CREATED"
    SEEDED = "SEEDED"
    RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
    INSTALLED_PENDING_UAT = "INSTALLED_PENDING_UAT"
    PRODUCTION_ACCEPTED = "PRODUCTION_ACCEPTED"


PHASE_ORDER = tuple(BootstrapPhase)
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_ORDER)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class BootstrapFailure:
    code: str
    stage: str
    occurred_at: str


@dataclass(slots=True)
class BootstrapState:
    version: int
    installation_id: str
    phase: BootstrapPhase
    created_at: str
    updated_at: str
    retry_count: int = 0
    config_fingerprint: str | None = None
    backend_commit: str | None = None
    admin_commit: str | None = None
    release_manifest_sha256: str | None = None
    recovery_object_key: str | None = None
    recovery_sha256: str | None = None
    last_failure: BootstrapFailure | None = field(default=None)

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BootstrapStateError("bootstrap control path is not a directory")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise BootstrapStateError("bootstrap control directory must use mode 0700")


def _atomic_private_write(path: Path, payload: bytes) -> None:
    _private_directory(path.parent)
    temp_path = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temp_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, 0o600, follow_symlinks=False)
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class BootstrapStateStore:
    def __init__(self, control_dir: Path, signing_key: bytes) -> None:
        self.control_dir = control_dir
        self.state_path = control_dir / "state.json"
        self.lock_path = control_dir / "state.lock"
        self.signing_key = signing_key
        _private_directory(control_dir)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            os.chmod(self.lock_path, 0o600, follow_symlinks=False)
            with os.fdopen(descriptor, "r+b", closefd=True) as lock_stream:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        finally:
            # fdopen owns and closes the descriptor in the normal path.
            pass

    def _signature(self, payload: dict[str, object]) -> str:
        return hmac.new(
            self.signing_key,
            _canonical_json(payload),
            hashlib.sha256,
        ).hexdigest()

    def _save_unlocked(self, state: BootstrapState) -> None:
        payload = state.public_dict()
        envelope = {"payload": payload, "signature": self._signature(payload)}
        _atomic_private_write(self.state_path, _canonical_json(envelope) + b"\n")

    def _load_unlocked(self) -> BootstrapState:
        try:
            raw = self.state_path.read_bytes()
            envelope = json.loads(raw)
            payload = envelope["payload"]
            signature = envelope["signature"]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise BootstrapStateError("bootstrap state cannot be read") from exc
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise BootstrapStateError("bootstrap state envelope is invalid")
        if not hmac.compare_digest(signature, self._signature(payload)):
            raise BootstrapStateError("bootstrap state signature is invalid")
        try:
            failure_payload = payload.get("last_failure")
            failure = (
                BootstrapFailure(**failure_payload)
                if isinstance(failure_payload, dict)
                else None
            )
            return BootstrapState(
                version=int(payload["version"]),
                installation_id=str(payload["installation_id"]),
                phase=BootstrapPhase(str(payload["phase"])),
                created_at=str(payload["created_at"]),
                updated_at=str(payload["updated_at"]),
                retry_count=int(payload.get("retry_count", 0)),
                config_fingerprint=payload.get("config_fingerprint"),
                backend_commit=payload.get("backend_commit"),
                admin_commit=payload.get("admin_commit"),
                release_manifest_sha256=payload.get("release_manifest_sha256"),
                recovery_object_key=payload.get("recovery_object_key"),
                recovery_sha256=payload.get("recovery_sha256"),
                last_failure=failure,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise BootstrapStateError("bootstrap state payload is invalid") from exc

    def initialize(self) -> BootstrapState:
        with self._locked():
            if self.state_path.exists():
                return self._load_unlocked()
            now = utc_now()
            state = BootstrapState(
                version=1,
                installation_id=secrets.token_hex(16),
                phase=BootstrapPhase.NEW,
                created_at=now,
                updated_at=now,
            )
            self._save_unlocked(state)
            return state

    def load(self, *, allow_completed: bool = True) -> BootstrapState:
        with self._locked():
            state = self._load_unlocked()
        if not allow_completed and PHASE_INDEX[state.phase] >= PHASE_INDEX[
            BootstrapPhase.INSTALLED_PENDING_UAT
        ]:
            raise BootstrapCompletedError("bootstrap has already completed")
        return state

    def transition(
        self,
        expected: BootstrapPhase,
        target: BootstrapPhase,
        **updates: str | int | None,
    ) -> BootstrapState:
        if PHASE_INDEX[target] != PHASE_INDEX[expected] + 1:
            raise BootstrapStateError("bootstrap transitions must advance one phase")
        with self._locked():
            state = self._load_unlocked()
            if state.phase != expected:
                raise BootstrapStateError(
                    f"bootstrap phase is {state.phase.value}, expected {expected.value}"
                )
            for name, value in updates.items():
                if not hasattr(state, name) or name in {
                    "version",
                    "installation_id",
                    "phase",
                    "created_at",
                }:
                    raise BootstrapStateError("unsupported bootstrap state update")
                setattr(state, name, value)
            state.phase = target
            state.updated_at = utc_now()
            state.last_failure = None
            self._save_unlocked(state)
            return state

    def record_failure(self, code: str, stage: str) -> BootstrapState:
        if not code or not stage:
            raise BootstrapStateError("failure code and stage are required")
        with self._locked():
            state = self._load_unlocked()
            if PHASE_INDEX[state.phase] >= PHASE_INDEX[
                BootstrapPhase.INSTALLED_PENDING_UAT
            ]:
                raise BootstrapCompletedError("bootstrap has already completed")
            state.retry_count += 1
            state.updated_at = utc_now()
            state.last_failure = BootstrapFailure(
                code=code[:64],
                stage=stage[:64],
                occurred_at=state.updated_at,
            )
            self._save_unlocked(state)
            return state

    def clear_failure(self) -> BootstrapState:
        with self._locked():
            state = self._load_unlocked()
            if PHASE_INDEX[state.phase] >= PHASE_INDEX[
                BootstrapPhase.INSTALLED_PENDING_UAT
            ]:
                raise BootstrapCompletedError("bootstrap has already completed")
            state.last_failure = None
            state.updated_at = utc_now()
            self._save_unlocked(state)
            return state
