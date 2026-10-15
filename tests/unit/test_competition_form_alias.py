"""竞赛动态表单 validate_field_values 的固定字段别名键行为。"""

from app.schemas.competition_form import validate_field_values


def _fields():
    return [
        {"key": "name", "label": "姓名", "type": "text", "required": True},
        {"key": "gender", "label": "性别", "type": "radio", "required": True, "options": ["男", "女"]},
        {"key": "phone_number", "label": "电话号码", "type": "phone", "required": True},
        {"key": "school", "label": "学校", "type": "text", "required": True},
    ]


def test_alias_keys_are_satisfied_by_top_level_fields():
    """姓名/电话号码/学校与固定三项同义，缺失时不得报必填错误。"""
    values = {"gender": "女"}
    validated = validate_field_values(_fields(), values)
    # 别名键不进 custom_field_values，避免与顶层字段重复存储
    assert validated == {"gender": "女"}


def test_alias_values_submitted_by_old_clients_are_dropped():
    """旧版本把别名键塞进 custom 提交时，静默丢弃不报错。"""
    values = {
        "gender": "女",
        "name": "伍胜男",
        "phone_number": "17748493418",
        "school": "成都工业职业技术学院",
    }
    validated = validate_field_values(_fields(), values)
    assert validated == {"gender": "女"}


def test_non_alias_required_still_enforced():
    import pytest

    from app.port.exceptions import ValidationException

    with pytest.raises(ValidationException, match="性别为必填项"):
        validate_field_values(_fields(), {"gender": ""})
