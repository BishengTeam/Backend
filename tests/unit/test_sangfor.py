from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.port.exceptions import BusinessException
from app.schemas.sangfor import SangforCreate, SangforExport
from app.services.sangfor import export_workbook
from app.services.sangfor_catalog import DIRECTIONS, HEADERS, require_direction


def submission(**changes):
    return dict(batch_id=1, phone="13800000000", organization="测试学院", first_name="Xiaofu", last_name="Shen", exam_voucher_code="VENDOR-001") | changes


def test_only_unambiguous_vendor_directions_are_open():
    assert set(DIRECTIONS) == {"SCSA-S", "SCSA-T", "SCCA-C", "SCSP-S", "SCCP-C", "SCSE-T", "SCSE-S", "SCCE-C"}
    for code in ("SCSP-T", "SCSP-SOP", "SCSP-SOF", "SCCP-ST", "SCCP-S", "SCCA-LB", "SCCP-LB", "micro"):
        with pytest.raises(BusinessException, match="尚未确认"):
            require_direction(code)


@pytest.mark.parametrize("key", ["organization", "first_name", "last_name", "exam_voucher_code"])
def test_required_vendor_fields_reject_whitespace(key):
    with pytest.raises(ValidationError):
        SangforCreate(**submission(**{key: "   "}))


@pytest.mark.parametrize("changes", [{"phone": "138"}, {"first_name": "小服"}, {"email": "bad"}, {"candidate_name": "他人"}, {"verification_code": "fake"}])
def test_new_submission_rejects_invalid_or_untrusted_fields(changes):
    with pytest.raises(ValidationError):
        SangforCreate(**submission(**changes))


def test_optional_contact_fields_and_no_student_documents():
    value = SangforCreate(**submission())
    assert value.email == value.mailing_address == ""
    assert not {"student_proof", "verification_code", "coupon_code"} & set(value.model_dump())


def test_export_is_actual_thirteen_columns_with_typed_date_text_ids_and_formula_safe_voucher():
    snapshot = SangforCreate(**submission(exam_voucher_code="=2+2")).model_dump(exclude={"batch_id"}) | {
        "name": "实名考生", "id_card": "11010519491231002X", "exam_date": "2026-07-28T02:00:00+00:00", "exam_direction": DIRECTIONS["SCSA-T"]}
    book = load_workbook(BytesIO(export_workbook([snapshot])))
    sheet = book["Sheet1"]
    assert [sheet.cell(2, column).value for column in range(1, 14)] == HEADERS
    assert sheet["A3"].value == "实名考生"
    assert sheet["B3"].value == "11010519491231002X" and sheet["B3"].data_type == "s"
    assert sheet["D3"].value == datetime(2026, 7, 28, 10)
    assert sheet["D3"].number_format == "yyyy-mm-dd"
    assert sheet["H3"].value == "Xiaofu" and sheet["I3"].value == "Shen"
    assert sheet["J3"].value == DIRECTIONS["SCSA-T"]
    assert sheet["K3"].value == "=2+2" and sheet["K3"].data_type == "s"
    assert sheet["A4"].value is None


@pytest.mark.parametrize("ids", [[], [1, 1], [0], [-1]])
def test_export_selection_must_be_explicit_valid_unique(ids):
    with pytest.raises(ValidationError):
        SangforExport(registration_ids=ids)


def test_sangfor_http_only_opens_public_batches(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.adapter.database import get_db
    from app.api.sangfor import router, service
    from app.middleware.error_handler import app_exception_handler
    from app.port.exceptions import AppException
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_exception_handler(AppException, app_exception_handler)
    async def db():
        yield SimpleNamespace()
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(service, "batches", AsyncMock(return_value=[]))
    client = TestClient(app)
    assert client.get("/api/sangfor/batches").status_code == 200
    assert client.get("/api/sangfor/registrations").status_code == 401
    assert client.get("/api/sangfor/registrations/1").status_code == 401
    assert client.post("/api/sangfor/registrations", json=submission()).status_code == 401
    assert client.post("/api/sangfor/registrations/1/resubmit", json={}).status_code == 401
