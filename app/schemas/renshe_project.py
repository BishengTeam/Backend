from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MaterialRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(..., max_length=64)
    name: str = Field(..., min_length=1, max_length=64)
    required: bool = True
    extensions: list[str] = Field(default_factory=list, max_length=5)
    max_count: int = Field(1, ge=1, le=10)


class RensheProjectConfiguration(BaseModel):
    """Unknown capabilities can be drafted; validation gates their use."""

    model_config = ConfigDict(extra="forbid")
    occupation_name: str = Field("", max_length=64)
    occupation_code: str = Field("", max_length=160)
    skill_level: str = Field("", max_length=32)
    export_skill_level: str = Field("", max_length=32)
    exam_types: list[str] = Field(default_factory=list, max_length=10)
    application_types: list[str] = Field(default_factory=list, max_length=10)
    qualification_rule: str = Field("", max_length=64)
    education_levels: list[str] = Field(default_factory=list, max_length=10)
    form_fields: list[str] = Field(default_factory=list, max_length=20)
    materials: list[MaterialRequirement] = Field(default_factory=list, max_length=20)
    export_template: str = Field("", max_length=64)
    field_mapping: dict[str, str] = Field(default_factory=dict, max_length=30)
    attachment_layout: str = Field("by_candidate", max_length=64)
    institution_types: list[str] = Field(default_factory=list, max_length=3)
    reporting_policy: dict = Field(default_factory=dict)
    commitment_template: dict | None = None


class RensheProjectWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chinese_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    home_cover_url: str | None = Field(None, max_length=512)
    status: Literal["draft", "available", "disabled"] = "draft"
    # Rule and material definitions are code-owned. Admins send only the
    # commitment file (if any); omitting this object selects the current rules.
    config: RensheProjectConfiguration | None = None
    expected_version: int | None = Field(None, ge=1)

    @field_validator("chinese_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class RensheProjectResponse(BaseModel):
    id: int
    code: str
    chinese_name: str
    description: str | None = None
    home_cover_url: str | None = None
    status: Literal["draft", "available", "disabled"]
    config_id: int | None = None
    version: int = 0
    config: RensheProjectConfiguration
    validation_errors: list[str] = Field(default_factory=list)
    batch_count: int = 0
