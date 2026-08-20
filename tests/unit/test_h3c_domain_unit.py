from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace


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
