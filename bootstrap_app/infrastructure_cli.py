from __future__ import annotations

import asyncio
import json

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.infrastructure import assert_empty_infrastructure
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


async def _main() -> None:
    settings = BootstrapSettings.from_env()
    store = BootstrapStateStore(settings.control_dir, settings.token)
    state = store.load(allow_completed=False)
    if state.phase == BootstrapPhase.INFRA_READY:
        print(json.dumps({"status": "already_ready"}, sort_keys=True))
        return
    if state.phase != BootstrapPhase.QUALITY_PASSED:
        raise SystemExit("infrastructure check refused: phase must be QUALITY_PASSED")
    result = await assert_empty_infrastructure(settings.installation_dir)
    store.transition(BootstrapPhase.QUALITY_PASSED, BootstrapPhase.INFRA_READY)
    print(json.dumps({"status": "ok", **result}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
