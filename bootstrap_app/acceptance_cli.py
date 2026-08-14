"""Host-orchestrated registration and post-UAT state reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from bootstrap_app.acceptance import (
    collect_runtime_evidence,
    read_database_acceptance_status,
    register_installed_acceptance,
)
from bootstrap_app.config import BootstrapSettings
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register deployment acceptance evidence or reconcile its terminal state."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument(
        "--backend-base-url",
        default=os.getenv("BOOTSTRAP_BACKEND_BASE_URL", "http://app:8000"),
    )
    subparsers.add_parser("sync-state")
    return parser


async def _run() -> dict[str, object]:
    args = _parser().parse_args()
    settings = BootstrapSettings.from_env()
    store = BootstrapStateStore(settings.control_dir, settings.token)
    state = store.load()
    if args.command == "register":
        if state.phase != BootstrapPhase.RECOVERY_VERIFIED:
            raise SystemExit(
                "acceptance registration refused: phase must be RECOVERY_VERIFIED"
            )
        runtime_evidence = await asyncio.to_thread(
            collect_runtime_evidence,
            args.backend_base_url,
        )
        acceptance_id = await register_installed_acceptance(
            installation_dir=settings.installation_dir,
            control_dir=settings.control_dir,
            state=state,
            runtime_evidence=runtime_evidence,
        )
        return {
            "status": "installed_pending_uat",
            "acceptance_id": acceptance_id,
            "installation_id": state.installation_id,
        }

    if state.phase == BootstrapPhase.PRODUCTION_ACCEPTED:
        return {
            "status": "production_accepted",
            "installation_id": state.installation_id,
        }
    if state.phase != BootstrapPhase.INSTALLED_PENDING_UAT:
        raise SystemExit(
            "acceptance state sync refused: phase must be INSTALLED_PENDING_UAT"
        )
    database_status = await read_database_acceptance_status(
        settings.installation_dir,
        state.installation_id,
    )
    if database_status != "production_accepted":
        return {
            "status": "installed_pending_uat",
            "installation_id": state.installation_id,
        }
    transitioned = store.transition(
        BootstrapPhase.INSTALLED_PENDING_UAT,
        BootstrapPhase.PRODUCTION_ACCEPTED,
    )
    return {
        "status": "production_accepted",
        "installation_id": transitioned.installation_id,
    }


def main() -> None:
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
