#!/usr/bin/env python3
"""从厂商价格表 xlsx 导入认证产品目录。

用法:
    python scripts/import_cert_catalog.py                       # 导入 docs/h3c/考试认证价格.xlsx 全部 H3C 条目到目录
    python scripts/import_cert_catalog.py --instantiate GB0-192 # 同时创建指定编码的产品+默认价格
    python scripts/import_cert_catalog.py --file 其他价格表.xlsx

规则:
    - 目录 upsert（type+code 冲突时更新考试属性，不动已建产品）
    --instantiate 仅创建缺失的产品；原价→normal 档，网院优惠券→student 档
    - 幂等，可重复执行
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.adapter.database import get_db_ctx  # noqa: E402
from app.models.cert_product import CertProduct  # noqa: E402
from app.models.cert_product_catalog import CertProductCatalog  # noqa: E402
from app.domain.order.src.index import PriceConfig  # noqa: E402

DEFAULT_FILE = ROOT / "docs" / "h3c" / "考试认证价格.xlsx"
SHEET_NAME = "新华三考试详情"
CERT_TYPE = "h3c"


@dataclass
class CatalogRow:
    code: str
    name: str
    duration_minutes: int | None
    question_count: int | None
    total_score: int | None
    pass_score: int | None
    cert_validity_years: int | None
    retake_count: int | None
    prerequisite: str | None
    remark: str | None
    normal_price_yuan: int
    student_price_yuan: int | None


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_sheet(path: Path) -> list[CatalogRow]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET_NAME]
    rows = list(ws.iter_rows(values_only=True))
    parsed: dict[str, CatalogRow] = {}
    current: CatalogRow | None = None
    for row in rows[1:]:
        subject = str(row[2]).strip() if row[2] else ""
        code = str(row[4]).strip() if row[4] else ""
        price_kind = str(row[5]).strip() if row[5] else ""
        price_yuan = _to_int(row[6])
        if code and code.startswith("GB"):
            current = CatalogRow(
                code=code,
                name=subject or code,
                duration_minutes=_to_int(row[9]),
                question_count=_to_int(row[10]),
                total_score=_to_int(row[11]),
                pass_score=_to_int(row[12]),
                cert_validity_years=_to_int(row[13]),
                retake_count=_to_int(row[7]),
                prerequisite=str(row[8]).strip() if row[8] else None,
                remark=str(row[15]).strip() if row[15] else None,
                normal_price_yuan=price_yuan or 0,
                student_price_yuan=None,
            )
            parsed[code] = current
        elif current is not None and price_kind == "网院优惠券" and price_yuan:
            current.student_price_yuan = price_yuan
    return list(parsed.values())


async def run(path: Path, instantiate: list[str]) -> None:
    rows = parse_sheet(path)
    print(f"parsed {len(rows)} catalog rows from {path}")
    async with get_db_ctx() as db:
        async with db.begin():
            for row in rows:
                catalog = (
                    await db.execute(
                        select(CertProductCatalog).where(
                            CertProductCatalog.type == CERT_TYPE,
                            CertProductCatalog.code == row.code,
                        )
                    )
                ).scalar_one_or_none()
                if catalog is None:
                    catalog = CertProductCatalog(
                        type=CERT_TYPE, code=row.code, name=row.name
                    )
                    db.add(catalog)
                    await db.flush()
                    print(f"  + catalog {row.code} {row.name}")
                catalog.name = row.name
                catalog.duration_minutes = row.duration_minutes
                catalog.question_count = row.question_count
                catalog.total_score = row.total_score
                catalog.pass_score = row.pass_score
                catalog.cert_validity_years = row.cert_validity_years
                catalog.retake_count = row.retake_count
                catalog.prerequisite = row.prerequisite
                catalog.remark = row.remark
                catalog.source = path.name

                if row.code in instantiate:
                    product = (
                        await db.execute(
                            select(CertProduct).where(CertProduct.code == row.code)
                        )
                    ).scalar_one_or_none()
                    if product is None:
                        db.add(
                            CertProduct(
                                type=CERT_TYPE,
                                catalog_id=catalog.id,
                                code=row.code,
                                name=code_slug(row.code),
                                chinese_name=row.name,
                                is_active=True,
                                sort_order=0,
                            )
                        )
                        print(f"  + product {row.code}")
                    await upsert_price(db, row.code, "normal", row.normal_price_yuan)
                    if row.student_price_yuan is not None:
                        await upsert_price(
                            db, row.code, "student", row.student_price_yuan
                        )
    print("done")


def code_slug(code: str) -> str:
    return code.lower().replace("-", "_")


async def upsert_price(db, code: str, user_type: str, yuan: int) -> None:
    cents = int(yuan) * 100
    row = (
        await db.execute(
            select(PriceConfig).where(
                PriceConfig.product_type == code,
                PriceConfig.user_type == user_type,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            PriceConfig(
                product_type=code, user_type=user_type, price=cents, is_active=True
            )
        )
    else:
        row.price = cents
        row.is_active = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument(
        "--instantiate",
        nargs="*",
        default=[],
        help="同时创建为产品的科目代码列表，如 GB0-192 GB0-713",
    )
    args = parser.parse_args()
    asyncio.run(run(args.file, args.instantiate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
