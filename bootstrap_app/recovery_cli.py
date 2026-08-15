from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.recovery import (
    MAX_ENVELOPE_BYTES,
    RecoveryBundleError,
    create_recovery_envelope,
    decrypt_recovery_envelope,
    recovery_oss_is_configured,
    restore_recovery_files,
    store_local_recovery_envelope,
    upload_recovery_envelope,
)
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypt, upload, or restore bootstrap recovery bundles.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("upload")

    decrypt = subparsers.add_parser("decrypt")
    decrypt.add_argument("--envelope", required=True, type=Path)
    decrypt.add_argument("--private-key", required=True, type=Path)
    decrypt.add_argument("--output-dir", required=True, type=Path)
    return parser


def _read_private_key(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RecoveryBundleError("recovery private key is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RecoveryBundleError("recovery private key path is unsafe")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RecoveryBundleError("recovery private key permissions must be 0600")
    if info.st_size > 64 * 1024:
        raise RecoveryBundleError("recovery private key is too large")
    return path.read_bytes()


def main() -> None:
    args = _parser().parse_args()
    if args.command == "decrypt":
        envelope = args.envelope.read_bytes()
        if len(envelope) > MAX_ENVELOPE_BYTES:
            raise SystemExit("recovery refused: envelope is too large")
        payload = decrypt_recovery_envelope(envelope, _read_private_key(args.private_key))
        restore_recovery_files(payload, args.output_dir.absolute())
        print(
            json.dumps(
                {
                    "status": "restored",
                    "installation_id": payload["installation_id"],
                    "output_dir": str(args.output_dir.absolute()),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return

    settings = BootstrapSettings.from_env()
    state_store = BootstrapStateStore(settings.control_dir, settings.token)
    state = state_store.load(allow_completed=False)
    if state.phase != BootstrapPhase.SEEDED:
        raise SystemExit("recovery upload refused: bootstrap phase must be SEEDED")
    envelope, digest = create_recovery_envelope(
        installation_dir=settings.installation_dir,
        control_dir=settings.control_dir,
        installation_id=state.installation_id,
        signing_key=settings.token,
    )
    if recovery_oss_is_configured(settings.installation_dir):
        object_key = upload_recovery_envelope(
            installation_dir=settings.installation_dir,
            installation_id=state.installation_id,
            envelope_bytes=envelope,
            envelope_sha256=digest,
        )
        result_status = "uploaded"
        recovery_oss_status = "configured"
    else:
        object_key = store_local_recovery_envelope(
            control_dir=settings.control_dir,
            installation_id=state.installation_id,
            envelope_bytes=envelope,
            envelope_sha256=digest,
        )
        result_status = "stored_locally"
        recovery_oss_status = "not_configured"
    state_store.transition(
        BootstrapPhase.SEEDED,
        BootstrapPhase.RECOVERY_VERIFIED,
        recovery_object_key=object_key,
        recovery_sha256=digest,
    )
    print(
        json.dumps(
            {
                "status": result_status,
                "object_key": object_key,
                "sha256": digest,
                "recovery_oss": recovery_oss_status,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
