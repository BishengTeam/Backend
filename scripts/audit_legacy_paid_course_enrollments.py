"""Export paid course enrollments that need historical payment reconciliation.

Run after applying the course purchase migration:
    python scripts/audit_legacy_paid_course_enrollments.py --output course-audit.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from contextlib import nullcontext
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


AUDIT_QUERY = text(
    """
    SELECT enrollment.id AS enrollment_id,
           enrollment.user_id,
           enrollment.course_id,
           course.title AS course_title,
           course.price AS course_price,
           course.video_url AS legacy_video_url,
           enrollment.status,
           enrollment.learning_access,
           enrollment.order_id,
           enrollment.created_at,
           CASE
               WHEN enrollment.order_id IS NULL THEN 'missing_order_link'
               ELSE 'linked_order'
           END AS audit_result,
           CASE
               WHEN course.video_url IS NOT NULL THEN TRUE
               ELSE FALSE
           END AS asset_migration_required
    FROM course_enrollment AS enrollment
    JOIN course ON course.id = enrollment.course_id
    WHERE course.price > 0
    ORDER BY enrollment.created_at, enrollment.id
    """
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export historical paid course enrollment audit data."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV output path. Defaults to stdout.",
    )
    return parser


def run(output: Path | None) -> int:
    from app.port.config import settings

    engine = create_engine(settings.DATABASE_URL_SYNC)
    try:
        with engine.connect() as connection:
            rows = connection.execute(AUDIT_QUERY).mappings().all()
    finally:
        engine.dispose()

    context = (
        output.open("w", encoding="utf-8-sig", newline="")
        if output is not None
        else nullcontext(sys.stdout)
    )
    fieldnames = [
        "enrollment_id",
        "user_id",
        "course_id",
        "course_title",
        "course_price",
        "legacy_video_url",
        "status",
        "learning_access",
        "order_id",
        "created_at",
        "audit_result",
        "asset_migration_required",
    ]
    with context as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    count = run(args.output)
    if args.output is not None:
        print(f"exported {count} paid course enrollment rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
