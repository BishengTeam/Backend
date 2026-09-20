from __future__ import annotations

import hashlib
import asyncio
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

from openpyxl import load_workbook
from openpyxl.drawing.spreadsheet_drawing import TwoCellAnchor
from PIL import Image as PillowImage


from app.services.h3c_export import H3C_TEMPLATE_SHA256, H3cExportService


ROOT = Path(__file__).resolve().parents[2]


def test_official_h3c_templates_are_versioned_and_unchanged():
    for registration_type, expected_digest in H3C_TEMPLATE_SHA256.items():
        path = ROOT / "app" / "templates" / "h3c" / f"{registration_type}.xlsx"
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest


def test_h3c_workbook_rows_match_official_template_columns():
    service = H3cExportService(storage=SimpleNamespace())
    batch = SimpleNamespace(
        exam_code="GB0-192",
        country="CHN",
        language="CHS",
        identity_tag="社会考生",
        training_org="智天远",
        training_teacher="王老师",
        training_address="成都",
        training_start="2026-09-01T00:00:00+00:00",
        training_end="2026-10-01T00:00:00+00:00",
    )
    registration = SimpleNamespace(
        candidate_snapshot={
            "candidate_name": "王小龙",
            "gender": "男",
            "candidate_idcard": "510101200001011234",
            "school": "四川智天远",
            "address": "成都市高新区",
            "phone": "13800138000",
            "email": "user@example.com",
            "education": "大学本科",
            "first_name_en": "Xiaolong",
            "last_name_en": "Wang",
            "coupon_code": "COUPON-001",
            "verify_code": "XUEXIN-001",
            "birth_date": "2000/01/01",
            "exam_datetime": "2026/10/10 9:00",
        }
    )

    coupon = service._workbook_row(
        batch=batch,
        registration=registration,
        registration_type="coupon",
    )
    full = service._workbook_row(
        batch=batch,
        registration=registration,
        registration_type="full",
    )
    student = service._workbook_row(
        batch=batch,
        registration=registration,
        registration_type="student",
    )

    assert len(coupon) == 24
    assert len(full) == 21
    assert len(student) == 22
    assert coupon[0] == "COUPON-001"
    assert coupon[11] is None  # embedded image placeholder
    assert full[0] == "GB0-192"
    assert student[12] == "XUEXIN-001"


def test_h3c_workbook_replaces_samples_with_formatted_export_rows(tmp_path):
    class Storage:
        async def download_file(self, storage_key: str, destination: Path) -> None:
            PillowImage.new("RGB", (140, 180), "white").save(destination, "JPEG")

    service = H3cExportService(storage=Storage())
    batch = SimpleNamespace(
        exam_code="GB0-192",
        country="CHN",
        language="CHS",
        identity_tag="社会考生",
        training_org="智天远教育科技有限公司",
        training_teacher="王老师",
        training_address="成都市高新区",
        training_start=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc),
        training_end=datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc),
    )
    registration = SimpleNamespace(
        id=123,
        candidate_snapshot={
            "candidate_name": "王小龙",
            "gender": "男",
            "candidate_idcard": "510101200001011234",
            "school": "四川智天远",
            "address": "成都市高新区",
            "phone": "13800138000",
            "email": "user@example.com",
            "education": "大学本科",
            "first_name_en": "Xiaolong",
            "last_name_en": "Wang",
            "verify_code": "XUEXIN-001",
            "birth_date": "2000/01/01",
            "exam_datetime": "2026-10-10T01:00:00+00:00",
        },
        materials=(
            SimpleNamespace(
                id=1,
                material_type="student_proof",
                storage_key="materials/1.jpg",
            ),
        ),
    )

    output = asyncio.run(
        service._build_workbook(
            batch=batch,
            registration_type="student",
            rows=[SimpleNamespace(registration=registration, materials=registration.materials)],
            destination=tmp_path,
        )
    )
    worksheet = load_workbook(output)["模板"]

    assert len(worksheet._images) == 1
    assert isinstance(worksheet._images[0].anchor, TwoCellAnchor)
    assert worksheet._images[0].anchor._from.col == 11
    assert worksheet._images[0].anchor._from.row == 2
    assert worksheet._images[0].anchor.to.col == 12
    assert worksheet._images[0].anchor.to.row == 3
    assert 160 < worksheet.row_dimensions[3].height < 167
    assert worksheet["A3"].value == "GB0-192"
    assert worksheet["B3"].value == "王小龙"
    assert worksheet["S3"].value == "智天远教育科技有限公司"
    assert worksheet.column_dimensions["S"].width > 20
    assert worksheet["N3"].value == datetime(2000, 1, 1)
    assert worksheet["N3"].number_format == "yyyy/m/d"
    assert worksheet["Q3"].value == datetime(2026, 10, 10, 9, 0)
    assert worksheet["Q3"].number_format == "yyyy/m/d h:mm"
    assert worksheet["U3"].value == datetime(2026, 10, 1)
    assert worksheet["U3"].number_format == "yyyy/m/d"
