"""Close expired pending orders.

Run from the repository root:
    python scripts/close_expired_orders.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_TIMEOUT_CLOSE_REASON = "payment_timeout"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Close expired pending orders.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of expired pending orders to close in this run.",
    )
    parser.add_argument(
        "--reason",
        default=DEFAULT_TIMEOUT_CLOSE_REASON,
        help="close_reason value written to closed orders.",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    from app.services.order_timeout import OrderTimeoutCloseService

    result = await OrderTimeoutCloseService().close_expired_pending_orders(
        limit=args.limit,
        close_reason=args.reason,
    )
    print(
        "closed_expired_orders "
        f"scanned={result.scanned} "
        f"closed={result.closed} "
        f"order_ids={result.order_ids}"
    )
    return result.closed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
