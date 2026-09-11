"""Backfill missing agreement template cover thumbnails.

Run from the Backend repository root:
    python scripts/backfill_agreement_covers.py             # dry-run
    python scripts/backfill_agreement_covers.py --run       # generate covers
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import select, update

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault(
    "JWT_SECRET", "agreement-cover-backfill-placeholder-do-not-use-in-prod"
)

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import AGREEMENT_TEMPLATE_TYPES, AgreementTemplate
from app.services.agreement_template_cover import AgreementCoverService
from app.services.upload import UploadService


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="generate covers; omit for dry-run"
    )
    parser.add_argument(
        "--type",
        choices=AGREEMENT_TEMPLATE_TYPES,
        help="only backfill one agreement type",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="maximum rows to process"
    )
    return parser


async def _candidates(args: argparse.Namespace) -> list[AgreementTemplate]:
    statement = (
        select(AgreementTemplate)
        .where(AgreementTemplate.cover_url.is_(None))
        .order_by(
            AgreementTemplate.type.asc(),
            AgreementTemplate.version.desc(),
            AgreementTemplate.id.desc(),
        )
    )
    if args.type is not None:
        statement = statement.where(AgreementTemplate.type == args.type)
    if args.limit is not None:
        statement = statement.limit(args.limit)

    async with get_db_ctx() as db:
        return list((await db.scalars(statement)).all())


async def run(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    rows = await _candidates(args)
    mode = "run" if args.run else "dry-run"
    print(
        f"agreement_cover_backfill mode={mode} "
        f"type={args.type or 'all'} candidates={len(rows)}"
    )
    for row in rows:
        print(
            f"  candidate id={row.id} type={row.type} "
            f"version={row.version} status={row.status}"
        )

    if not args.run or not rows:
        return 0

    service = AgreementCoverService()
    succeeded = 0
    failed = 0
    skipped = 0
    for row in rows:
        try:
            cover_url = await service.generate(row.title, row.content)
            async with get_db_ctx() as db:
                result = await db.execute(
                    update(AgreementTemplate)
                    .where(
                        AgreementTemplate.id == row.id,
                        AgreementTemplate.cover_url.is_(None),
                    )
                    .values(cover_url=cover_url)
                )
                await db.commit()
        except Exception:
            failed += 1
            logger.exception(
                "agreement_cover_backfill_row_failed id=%s type=%s version=%s",
                row.id,
                row.type,
                row.version,
            )
            continue

        if result.rowcount == 0:
            skipped += 1
            UploadService.delete_file_by_url(cover_url)
            print(f"  skipped id={row.id}: cover_url was set by another process")
        else:
            succeeded += 1
            print(f"  generated id={row.id} cover_url={cover_url}")

    print(
        "agreement_cover_backfill_result "
        f"candidates={len(rows)} succeeded={succeeded} "
        f"failed={failed} skipped={skipped}"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
