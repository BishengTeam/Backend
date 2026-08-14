from __future__ import annotations

import argparse
import json

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advance the signed one-time bootstrap state from the host orchestrator."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")

    transition = subparsers.add_parser("transition")
    transition.add_argument("--from", dest="expected", required=True, choices=BootstrapPhase)
    transition.add_argument("--to", dest="target", required=True, choices=BootstrapPhase)
    transition.add_argument("--backend-commit")
    transition.add_argument("--admin-commit")
    transition.add_argument("--release-manifest-sha256")
    transition.add_argument("--recovery-object-key")
    transition.add_argument("--recovery-sha256")

    failure = subparsers.add_parser("failure")
    failure.add_argument("--code", required=True)
    failure.add_argument("--stage", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = BootstrapSettings.from_env()
    store = BootstrapStateStore(settings.control_dir, settings.token)
    store.initialize()
    if args.command == "status":
        state = store.load()
    elif args.command == "failure":
        state = store.record_failure(args.code, args.stage)
    else:
        updates = {
            key: value
            for key, value in {
                "backend_commit": args.backend_commit,
                "admin_commit": args.admin_commit,
                "release_manifest_sha256": args.release_manifest_sha256,
                "recovery_object_key": args.recovery_object_key,
                "recovery_sha256": args.recovery_sha256,
            }.items()
            if value is not None
        }
        state = store.transition(
            BootstrapPhase(args.expected),
            BootstrapPhase(args.target),
            **updates,
        )
    print(json.dumps(state.public_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
