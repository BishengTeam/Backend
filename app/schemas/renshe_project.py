from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    education_levels: list[str] = Field(default_factory=list, max_length=20)
    form_fields: list[str] = Field(default_factory=list, max_length=20)
    materials: list[MaterialRequirement] = Field(default_factory=list, max_length=20)
    export_template: str = Field("", max_length=64)
    field_mapping: dict[str, str] = Field(default_factory=dict, max_length=30)
    attachment_layout: str = Field("by_candidate", max_length=64)
    institution_types: list[str] = Field(default_factory=list, max_length=3)
    reporting_policy: dict = Field(default_factory=dict)
    commitment_template: dict | None = None
    selection_id: str | None = None
    rule_profile: dict = Field(default_factory=dict)


class RensheProjectWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chinese_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    home_cover_url: str | None = Field(None, max_length=512)
    status: Literal["draft", "available", "disabled"] = "draft"
    # Select an adapted occupation/level; all other rules come from the server.
    occupation_code: str | None = Field(None, max_length=160)
    selection_id: str | None = Field(None, max_length=100)
    skill_level: str | None = Field(None, max_length=32)
    commitment_template: dict | None = None
    # Compatibility for older clients. Omission preserves an existing project.
    config: RensheProjectConfiguration | None = None
    expected_version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def paired_selection(self):
        if self.selection_id is not None and (self.config is not None or self.occupation_code is not None or self.skill_level is not None):
            raise ValueError("报考组合标识不能与完整配置或旧版选择同时提交")
        if (self.occupation_code is None) != (self.skill_level is None):
            raise ValueError("请选择职业和对应技能等级")
        if self.occupation_code is not None and self.config is not None:
            raise ValueError("职业等级选择与完整配置不能同时提交")
        return self

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
    admission_requirements: dict = Field(default_factory=dict)
    export_instructions: dict | None = None
    validation_errors: list[str] = Field(default_factory=list)
    batch_count: int = 0
    review_notices: list[str] = Field(default_factory=list)
    commitment_template: dict | None = None
    commitment_template_error: str | None = None


class RensheCommitmentTemplateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(pattern=r"^commitment_[a-f0-9]{64}\.(pdf|docx)$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_filename: str | None = Field(None, max_length=255)
    size_bytes: int | None = Field(None, ge=1, le=10 * 1024 * 1024)
    content_type: str | None = Field(None, max_length=128)


class RensheCommitmentTemplateBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    commitment_template: RensheCommitmentTemplateReference
