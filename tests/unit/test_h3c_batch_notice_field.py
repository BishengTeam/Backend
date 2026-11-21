from app.schemas.h3c_registration import H3cExamBatchUpdate


def test_h3c_update_schema_keeps_registration_notice():
    payload = H3cExamBatchUpdate(
        registration_notice="报名完成后不可退款，请确认后提交。"
    ).model_dump(exclude_unset=True)

    assert payload == {"registration_notice": "报名完成后不可退款，请确认后提交。"}
