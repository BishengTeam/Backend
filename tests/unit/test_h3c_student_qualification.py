import pytest
from pydantic import ValidationError

from app.schemas.h3c_registration import H3cOrderCreate, H3cRejectRefundRequest, H3cResubmissionCreate
from app.services.h3c_registration import student_correction_scope


def payload(**overrides):
    return dict(batch_id=1, registration_type="student", candidate_name="测试人", gender="男",
                candidate_idcard="510101200001010123", school="测试学校", address="成都", phone="13800000001",
                email="a@example.com", education="本科", first_name_en="Test", last_name_en="User",
                student_verification_method="online_code", verify_code="CODE", student_proof_key="proof.jpg", **overrides)


@pytest.mark.parametrize("education", ["本科", "中职", "高职", "硕士", "博士"])
def test_h3c_supported_education(education):
    values = payload(); values["education"] = education
    assert H3cOrderCreate(**values).education == education


@pytest.mark.parametrize("education", ["大学本科", "其他", "", "专科"])
def test_h3c_education_rejects_unmapped_prefill(education):
    values = payload(); values["education"] = education
    with pytest.raises(ValidationError): H3cOrderCreate(**values)


@pytest.mark.parametrize("method,code", [("online_code", " CODE "), ("stamped_certificate", None)])
def test_both_student_paths(method, code):
    values = payload(); values.update(student_verification_method=method, verify_code=code)
    data = H3cOrderCreate(**values)
    assert data.verify_code == ("CODE" if code else None)


@pytest.mark.parametrize("change", [
    {"student_verification_method": None}, {"student_proof_key": None}, {"student_proof_key": "   "},
    {"verify_code": " "}, {"student_verification_method": "stamped_certificate", "verify_code": "CODE"},
    {"student_verification_method": "other"},
])
def test_incomplete_or_mixed_student_path_is_rejected(change):
    values = payload(); values.update(change)
    with pytest.raises(ValidationError): H3cOrderCreate(**values)


def test_resubmission_distinguishes_explicit_code_clear_from_omission():
    cleared = H3cResubmissionCreate(student_verification_method="stamped_certificate", verify_code=None, student_proof_key="new.jpg")
    omitted = H3cResubmissionCreate(student_verification_method="stamped_certificate", student_proof_key="new.jpg")
    assert "verify_code" in cleared.model_fields_set
    assert "verify_code" not in omitted.model_fields_set
    assert student_correction_scope("student", ["verify_code"], []) == ({"verify_code", "student_verification_method"}, {"student_proof"})
    assert student_correction_scope("student", ["phone"], []) == ({"phone"}, set())
    assert student_correction_scope("coupon", [], ["coupon_proof"]) == (set(), {"coupon_proof"})


@pytest.mark.parametrize("reason", ["", " ", "\n\t", "x" * 1001])
def test_blank_or_overlong_refund_reason_is_rejected(reason):
    with pytest.raises(ValidationError): H3cRejectRefundRequest(reason_code="review_rejected", reason_detail=reason)


def test_refund_reason_is_trimmed():
    assert H3cRejectRefundRequest(reason_code="review_rejected", reason_detail="  材料不符  ").reason_detail == "材料不符"
