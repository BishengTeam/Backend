"""Competition dynamic form field schemas and validation."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


FieldType = Literal[
    "text", "textarea", "number", "select", "radio", "checkbox",
    "date", "phone", "email", "idcard",
]

# Fields that need options list
OPTION_FIELDS = {"select", "radio", "checkbox"}

# Fields with max_length
TEXT_FIELDS = {"text", "textarea"}

# Fields with built-in validation
VALIDATED_FIELDS = {"phone", "email", "idcard"}

# Preset fields admins can quickly add
PRESET_FIELDS: list[dict] = [
    {"key": "student_id", "label": "学号", "type": "text", "required": True, "placeholder": "请输入学号", "max_length": 20},
    {"key": "grade", "label": "年级", "type": "select", "required": True, "options": ["大一", "大二", "大三", "大四", "研究生"]},
    {"key": "major", "label": "专业", "type": "text", "required": True, "placeholder": "请输入专业", "max_length": 64},
    {"key": "idcard", "label": "身份证号", "type": "idcard", "required": False, "placeholder": "18位身份证号"},
    {"key": "email", "label": "邮箱", "type": "email", "required": False, "placeholder": "用于接收通知"},
    {"key": "team_name", "label": "队伍名称", "type": "text", "required": True, "placeholder": "团队参赛必填", "max_length": 64},
    {"key": "team_size", "label": "团队人数", "type": "number", "required": True, "placeholder": "如：3"},
    {"key": "gender", "label": "性别", "type": "radio", "required": True, "options": ["男", "女"]},
    {"key": "birth_date", "label": "出生日期", "type": "date", "required": False},
    {"key": "address", "label": "通信地址", "type": "textarea", "required": False, "placeholder": "省市区街道", "max_length": 256},
    {"key": "emergency_contact", "label": "紧急联系人", "type": "text", "required": False, "placeholder": "姓名及电话", "max_length": 64},
    {"key": "skills", "label": "技能标签", "type": "checkbox", "required": False, "options": ["前端", "后端", "运维", "安全", "算法", "测试", "产品设计"]},
]


class FormFieldConfig(BaseModel):
    """Configuration for a single custom form field."""
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=32)
    type: FieldType
    required: bool = False
    placeholder: str | None = Field(None, max_length=64)
    max_length: int | None = Field(None, ge=1, le=500)
    options: list[str] | None = None
    sort_order: int = Field(default=0, ge=0)

    @field_validator("options")
    @classmethod
    def validate_options(cls, v, info):
        field_type = info.data.get("type")
        if field_type in OPTION_FIELDS:
            if not v or len(v) == 0:
                raise ValueError(f"{field_type} 类型必须提供选项列表")
            if len(v) > 20:
                raise ValueError("选项数量不能超过20个")
        return v

    @field_validator("max_length")
    @classmethod
    def validate_max_length(cls, v, info):
        field_type = info.data.get("type")
        if field_type in TEXT_FIELDS and v is None:
            return 128 if field_type == "text" else 1000
        return v


class CustomFieldsValidator(BaseModel):
    """Validate a list of form field configs."""
    fields: list[FormFieldConfig]

    @field_validator("fields")
    @classmethod
    def validate_no_duplicate_keys(cls, v):
        keys = [f.key for f in v]
        if len(keys) != len(set(keys)):
            duplicates = [k for k in keys if keys.count(k) > 1]
            raise ValueError(f"字段键重复: {', '.join(set(duplicates))}")
        return v


def validate_custom_fields(fields: list[dict] | None) -> list[dict] | None:
    """Validate and normalize custom fields JSON."""
    if not fields:
        return []
    result = CustomFieldsValidator(fields=fields)
    sorted_fields = sorted(result.fields, key=lambda f: f.sort_order)
    return [f.model_dump(exclude_none=True) for f in sorted_fields]


def validate_field_values(
    fields: list[dict] | None, values: dict | None
) -> dict:
    """Validate user-submitted values against field configs."""
    import re

    errors = []
    if not fields:
        if values:
            return {}
        return {}

    if values is None:
        values = {}

    validated = {}
    for field in fields:
        key = field["key"]
        field_type = field["type"]
        required = field.get("required", False)
        value = values.get(key)

        # Required check
        if required and (value is None or (isinstance(value, str) and not value.strip())):
            errors.append(f"{field['label']}为必填项")
            continue

        if value is None or (isinstance(value, str) and not value.strip()):
            continue

        # Type-specific validation
        if field_type == "number":
            try:
                validated[key] = int(value)
            except (ValueError, TypeError):
                try:
                    validated[key] = float(value)
                except (ValueError, TypeError):
                    errors.append(f"{field['label']}必须为数字")
        elif field_type == "phone":
            phone = str(value).strip()
            if not re.fullmatch(r"1[3-9]\d{9}", phone):
                errors.append(f"{field['label']}格式不正确")
            else:
                validated[key] = phone
        elif field_type == "email":
            email = str(value).strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                errors.append(f"{field['label']}格式不正确")
            else:
                validated[key] = email
        elif field_type == "idcard":
            idcard = str(value).strip().upper()
            if not re.fullmatch(r"\d{17}[\dX]", idcard):
                errors.append(f"{field['label']}必须为18位")
            else:
                validated[key] = idcard
        elif field_type in OPTION_FIELDS:
            options = field.get("options", [])
            if field_type == "checkbox":
                if isinstance(value, list):
                    invalid = [v for v in value if v not in options]
                    if invalid:
                        errors.append(f"{field['label']}包含无效选项")
                    else:
                        validated[key] = value
                else:
                    validated[key] = [str(value)]
            else:
                if str(value) not in options:
                    errors.append(f"{field['label']}选项无效")
                else:
                    validated[key] = str(value)
        else:
            # text, textarea, date
            max_len = field.get("max_length", 128)
            str_val = str(value).strip()
            if len(str_val) > max_len:
                errors.append(f"{field['label']}不能超过{max_len}字")
            else:
                validated[key] = str_val

    if errors:
        from app.port.exceptions import ValidationException
        raise ValidationException("; ".join(errors))

    return validated
