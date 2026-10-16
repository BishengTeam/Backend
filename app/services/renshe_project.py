"""Project capabilities and frozen batch admission policy.

Admission stays frozen on each batch. Exports use the current code contract;
commitment downloads use the project's current uploaded file.
"""

from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.plan.src.index import Plan
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import (
    EDUCATION_LEVELS,
    MATERIAL_SPECS,
    RENSHE_DEFAULT_PROGRAM,
    RensheAuditLog,
    find_renshe_program,
)
from app.models.cert_product import CertProduct
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.renshe_project import (
    RensheProjectConfiguration,
    RensheProjectResponse,
    RensheProjectWrite,
)
from app.services.renshe_graduated import (
    RULE, INSTITUTION_TYPES, MATERIALS as GRADUATED_MATERIALS, REVIEW_CHECKS,
    LEGACY_MATERIALS, is_graduated, policy_errors, activation_errors,
)
from app.services.renshe_reporting import EDUCATIONS, OCCUPATIONS, ETHNICITIES, POLITICS, DEGREE_CONDITION, HEADERS, default_policy

MATERIAL_NAMES = {
    "id_card_front": "身份证正面",
    "id_card_back": "身份证背面",
    "portrait": "二寸免冠照片",
    "student_card": "学生证",
    "xuexin_registration": "学信网电子注册表",
    "education_proof": "学历证明",
    **LEGACY_MATERIALS,
    **GRADUATED_MATERIALS,
}
FORM_FIELDS = ["contact_phone", "mailing_address", "email"]


def admission_requirements() -> dict:
    """The single read-only admission contract served to Admin."""
    config = graduated_configuration()
    return {
        "scope": {
            "occupation_name": config["occupation_name"],
            "occupation_code": config["occupation_code"],
            "skill_level": config["skill_level"],
            "export_skill_level": config["export_skill_level"],
            "application_types": config["application_types"],
            "exam_types": config["exam_types"],
            "qualification_rule": config["qualification_rule"],
        },
        "form_sections": ["本人实名信息", "毕业学历信息", "联系信息", "报送模板要求的其他实际信息"],
        "education_options": [
            {"value": value, "label": label}
            for value, label in EDUCATIONS.items()
        ],
        "institution_types": [
            {"value": value, "label": label}
            for value, label in INSTITUTION_TYPES.items()
        ],
        "materials": [
            {
                **item,
                "max_size_bytes": MATERIAL_SPECS[item["kind"]].max_bytes,
            }
            for item in config["materials"]
        ],
        "notes": [
            "文化程度选项全部展示，用于考生如实填报；是否符合本期资格仍由毕业证、院校类型、专业和初审共同判断。",
            "新批次使用当前代码规则；已发布批次继续使用创建时冻结的报名要求。",
            "材料格式、大小、数量和必填性由服务端强制校验。",
        ],
    }


def default_configuration() -> dict:
    program = asdict(RENSHE_DEFAULT_PROGRAM)
    return {
        **{
            key: program[key]
            for key in (
                "occupation_name",
                "occupation_code",
                "skill_level",
                "export_skill_level",
                "qualification_rule",
            )
        },
        "exam_types": [program["exam_type"]],
        "application_types": [program["application_type"]],
        "education_levels": sorted(EDUCATION_LEVELS),
        "form_fields": list(FORM_FIELDS),
        "materials": [
            {
                "kind": kind,
                "name": MATERIAL_NAMES[kind],
                "required": True,
                "extensions": sorted(spec.extensions),
                "max_count": 1,
            }
            for kind, spec in MATERIAL_SPECS.items() if kind in {
                "id_card_front", "id_card_back", "portrait", "student_card", "xuexin_registration", "education_proof"
            }
        ],
        "export_template": "renshe_registration_v1",
        "field_mapping": {},
        "attachment_layout": "by_candidate",
    }


def graduated_configuration() -> dict:
    config = default_configuration()
    config.update(
        qualification_rule=RULE,
        institution_types=list(INSTITUTION_TYPES),
        reporting_policy=default_policy(),
        education_levels=list(EDUCATIONS),
        materials=[{
            "kind": kind, "name": name, "required": True,
            "extensions": sorted(MATERIAL_SPECS[kind].extensions), "max_count": 1,
        } for kind, name in GRADUATED_MATERIALS.items()],
        export_template="renshe_graduated_v1",
        attachment_layout="flat",
    )
    return config


def configuration_errors(config: dict) -> list[str]:
    errors = []
    for key, label in (
        ("occupation_name", "职业／工种"),
        ("occupation_code", "职业代码"),
        ("skill_level", "技能等级"),
        ("export_skill_level", "等级导出值"),
    ):
        if not str(config.get(key) or "").strip():
            errors.append(f"请填写{label}")
    if not config.get("exam_types") or not set(config["exam_types"]) <= {
        "新考",
        "补考",
    }:
        errors.append("考试类型需要适配；当前支持新考、补考")
    if config.get("application_types") != ["学历型"]:
        errors.append("申报类型需要适配；当前支持学历型")
    graduated = is_graduated(config)
    if not graduated and config.get("qualification_rule") != "related_major_current_graduating_student":
        errors.append("资格规则尚未适配")
    if graduated:
        program = RENSHE_DEFAULT_PROGRAM
        if any(config.get(key) != getattr(program, key) for key in (
            "occupation_name", "occupation_code", "skill_level", "export_skill_level"
        )):
            errors.append("本期只支持信息安全管理员、四级／中级工")
        if not config.get("institution_types") or not set(config["institution_types"]) <= set(INSTITUTION_TYPES):
            errors.append("请选择本期支持的院校类型")
        from app.services.renshe_graduated import commitment_policy
        errors.extend(policy_errors(commitment_policy(config)))
    levels = config.get("education_levels", [])
    if not levels or not set(levels) <= (set(EDUCATIONS) if graduated else EDUCATION_LEVELS):
        errors.append("请选择已支持的学历范围")
    if set(config.get("form_fields", [])) != set(FORM_FIELDS):
        errors.append("报名字段需要适配；当前必须包含联系电话、通讯地址和邮箱")
    materials = config.get("materials", [])
    kinds = [item["kind"] for item in materials]
    if len(kinds) != len(set(kinds)):
        errors.append("材料类型不能重复")
    for item in materials:
        spec = MATERIAL_SPECS.get(item["kind"])
        if (
            spec is None
            or item.get("max_count") != 1
            or not item.get("extensions")
            or not set(item["extensions"]) <= spec.extensions
        ):
            errors.append(f"材料「{item['name']}」的类型、格式或数量需要适配")
    standard_graduated = graduated and set(kinds) == set(GRADUATED_MATERIALS)
    if graduated and not standard_graduated and set(kinds) != set(LEGACY_MATERIALS):
        errors.append("学历型材料清单需要适配")
    if not graduated and not set(kinds) <= {"id_card_front", "id_card_back", "portrait", "student_card", "xuexin_registration", "education_proof"}:
        errors.append("历史学生规则仅支持原六类材料")
    for kind in (
        GRADUATED_MATERIALS
        if standard_graduated
        else LEGACY_MATERIALS
        if graduated
        else ("id_card_front", "id_card_back", "portrait")
    ):
        if not any(item["kind"] == kind and item["required"] for item in materials):
            errors.append(f"{MATERIAL_NAMES[kind]}为必填材料")
    return errors


def batch_configuration(plan: Plan) -> dict:
    config = getattr(plan, "renshe_config", None)
    if config:
        errors = configuration_errors(config)
        if errors:
            raise ConflictException("批次配置不可用：" + "；".join(errors))
        if (
            plan.exam_type not in config["exam_types"]
            or plan.application_type not in config["application_types"]
            or any(
                getattr(plan, key) != config[key]
                for key in ("occupation_name", "occupation_code", "skill_level")
            )
        ):
            raise ConflictException("批次报考属性与固定配置不一致")
        return config
    # Only confirmed legacy combinations can use compatibility reads.
    if (
        plan.product_type == "RS-ZY"
        and all(
            getattr(plan, key, None)
            for key in (
                "occupation_name",
                "occupation_code",
                "skill_level",
                "exam_type",
                "application_type",
            )
        )
        and find_renshe_program(
            occupation_name=plan.occupation_name,
            occupation_code=plan.occupation_code,
            skill_level=plan.skill_level,
            exam_type=plan.exam_type,
            application_type=plan.application_type,
        )
        is not None
    ):
        return default_configuration()
    raise ConflictException("批次缺少有效项目配置，请核实历史归属")


async def is_renshe_plan(db, plan: Plan | None) -> bool:
    if plan is None:
        return False
    product = await db.scalar(
        select(CertProduct).where(CertProduct.code == plan.product_type)
    )
    if product is not None and product.type == "renshe":
        config_id = getattr(plan, "renshe_config_id", None)
        if config_id is not None:
            version = await db.get(RensheProjectConfig, config_id)
            if (
                version is None
                or version.product_id != product.id
                or version.config != plan.renshe_config
            ):
                raise ConflictException("批次配置版本与项目关联不一致")
        return True
    return product is None and plan.product_type == "RS-ZY"


class RensheProjectService:
    @staticmethod
    def capabilities() -> dict:
        from app.services.renshe_export_definition import export_instructions
        return {
            "default_config": graduated_configuration(),
            "templates": {"current": {"label": "当前固定25列报名信息模板", "columns": HEADERS}},
            "material_names": MATERIAL_NAMES,
            "form_fields": FORM_FIELDS,
            "qualification_rules": {
                RULE: "已取得毕业证的本专业或相关专业（学历型，人工核验）",
                "related_major_current_graduating_student": "历史规则：在读应届学生（仅保留历史批次）",
            },
            "institution_types": INSTITUTION_TYPES,
            "review_checks": REVIEW_CHECKS,
            "reporting_contract": {"education_labels": EDUCATIONS, "occupations": OCCUPATIONS, "ethnicities": ETHNICITIES, "political_statuses": POLITICS, "application_condition": DEGREE_CONDITION},
            "admission_requirements": admission_requirements(),
            "export_instructions": export_instructions(),
        }

    @staticmethod
    async def latest(db, product_id: int) -> RensheProjectConfig | None:
        return await db.scalar(
            select(RensheProjectConfig)
            .where(RensheProjectConfig.product_id == product_id)
            .order_by(RensheProjectConfig.version.desc())
            .limit(1)
        )

    async def _response(self, db, product: CertProduct) -> RensheProjectResponse:
        version = await self.latest(db, product.id)
        config = (
            version.config
            if version
            else {**default_configuration(), "occupation_name": "", "skill_level": ""}
        )
        return RensheProjectResponse(
            id=product.id,
            code=product.code,
            chinese_name=product.chinese_name,
            description=product.description,
            home_cover_url=product.home_cover_url,
            status=product.renshe_status
            or ("available" if product.is_active and version else "draft"),
            config_id=version.id if version else None,
            version=version.version if version else 0,
            config=RensheProjectConfiguration.model_validate(config),
            validation_errors=configuration_errors(config),
            batch_count=await db.scalar(
                select(func.count())
                .select_from(Plan)
                .where(Plan.product_type == product.code)
            )
            or 0,
        )

    async def list_projects(
        self, *, public: bool = False
    ) -> list[RensheProjectResponse]:
        async with get_db_ctx() as db:
            stmt = (
                select(CertProduct)
                .where(CertProduct.type == "renshe")
                .order_by(CertProduct.sort_order, CertProduct.id)
            )
            if public:
                stmt = stmt.where(
                    CertProduct.is_active.is_(True),
                    CertProduct.renshe_status == "available",
                )
            products = (await db.execute(stmt)).scalars().all()
            return [await self._response(db, product) for product in products]

    async def save(
        self, data: RensheProjectWrite, *, product_id: int | None = None, admin_id: int
    ) -> RensheProjectResponse:
        incoming = data.config.model_dump() if data.config is not None else {}
        from app.services.renshe_graduated import commitment_policy
        if data.config is None:
            # Metadata-only saves select the current code-owned admission
            # contract. An existing commitment file is preserved below.
            config = graduated_configuration()
            config["commitment_template"] = None
            config["reporting_policy"] = default_policy()
        else:
            # Full payloads remain compatible with persisted versions. The
            # current Admin sends the backend's default config for every save.
            config = incoming
            config["commitment_template"] = commitment_policy(config)["commitment_template"]
            config.update(
                export_template=(
                    "renshe_graduated_v1"
                    if is_graduated(config)
                    else "renshe_registration_v1"
                ),
                field_mapping={},
                attachment_layout="flat",
            )
            if is_graduated(config):
                config["reporting_policy"] = default_policy()
            else:
                config.pop("institution_types", None)
                config.pop("reporting_policy", None)
        async with get_db_ctx() as db:
            product = None
            if product_id is not None:
                product = await db.scalar(
                    select(CertProduct)
                    .where(CertProduct.id == product_id, CertProduct.type == "renshe")
                    .with_for_update()
                )
                if product is None:
                    raise NotFoundException("人社报考项目")
            if product is None:
                code = "RS-" + uuid4().hex[:24].upper()
                product = CertProduct(
                    type="renshe",
                    code=code,
                    name=code.lower().replace("-", "_"),
                    chinese_name=data.chinese_name,
                )
                db.add(product)
            await db.flush()
            latest = await self.latest(db, product.id)
            # A metadata-only save must not clear an existing commitment file.
            if data.config is None and latest is not None:
                config["commitment_template"] = latest.config.get("commitment_template")
            errors = configuration_errors(config)
            if data.status == "available" and errors:
                raise BusinessException("项目配置不可用：" + "；".join(errors))
            admission_changed = latest is None or any(latest.config.get(key) != config.get(key) for key in ("qualification_rule", "occupation_name", "occupation_code", "skill_level", "education_levels", "institution_types", "materials", "exam_types", "application_types"))
            if data.status == "available" and (is_graduated(config) or admission_changed or product.renshe_status != "available"):
                activation = activation_errors(config)
                if activation:
                    raise BusinessException("；".join(activation))
            changed_fields = [
                key
                for key, value in config.items()
                if latest is None or latest.config.get(key) != value
            ]
            if latest and data.expected_version != latest.version:
                raise ConflictException("项目配置已更新，请刷新后重试")
            if latest and any(
                latest.config.get(key) != config.get(key)
                for key in ("occupation_name", "skill_level")
            ):
                used = await db.scalar(
                    select(Plan.id).where(Plan.product_type == product.code).limit(1)
                )
                if used is not None:
                    raise BusinessException(
                        "已有关联批次的项目不能更换职业或等级，请新建项目"
                    )
            if latest is None or latest.config != config:
                version = RensheProjectConfig(
                    product_id=product.id,
                    version=(latest.version + 1 if latest else 1),
                    config=config,
                )
                db.add(version)
            product.chinese_name, product.description, product.home_cover_url = (
                data.chinese_name,
                data.description,
                data.home_cover_url,
            )
            product.renshe_status, product.is_active = (
                data.status,
                data.status == "available",
            )
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=admin_id,
                    action="project.save",
                    object_type="cert_product",
                    object_id=product.id,
                    result="succeeded",
                    summary={
                        "status": data.status,
                        "config": config,
                        "changed_fields": changed_fields,
                        "version": (
                            latest.version + 1 if changed_fields else latest.version
                        )
                        if latest
                        else 1,
                    },
                )
            )
            await db.commit()
            return await self._response(db, product)

    async def delete(self, product_id: int, *, admin_id: int) -> None:
        async with get_db_ctx() as db:
            product = await db.scalar(
                select(CertProduct)
                .where(CertProduct.id == product_id, CertProduct.type == "renshe")
                .with_for_update()
            )
            if product is None:
                raise NotFoundException("人社报考项目")
            if (
                product.renshe_status != "draft"
                or await db.scalar(
                    select(Plan.id).where(Plan.product_type == product.code).limit(1)
                )
                or await db.scalar(
                    select(Order.id).where(Order.product_type == product.code).limit(1)
                )
            ):
                raise BusinessException("仅无批次引用的草稿项目可删除")
            from sqlalchemy import delete

            await db.execute(
                delete(RensheProjectConfig).where(
                    RensheProjectConfig.product_id == product.id
                )
            )
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=admin_id,
                    action="project.delete",
                    object_type="cert_product",
                    object_id=product.id,
                    result="succeeded",
                    summary={"name": product.chinese_name},
                )
            )
            await db.delete(product)
            await db.commit()

    @classmethod
    async def bind(
        cls,
        db,
        plan: Plan,
        *,
        config_id: int | None = None,
        product_code: str | None = None,
        exam_type: str | None = None,
        application_type: str | None = None,
    ) -> None:
        code = product_code or plan.product_type
        product = await db.scalar(
            select(CertProduct)
            .where(CertProduct.code == code, CertProduct.type == "renshe")
            .with_for_update()
        )
        if product is None:
            raise NotFoundException("人社报考项目")
        if not product.is_active or product.renshe_status != "available":
            raise BusinessException("仅可用项目可以创建或更新批次配置")
        version = (
            await db.get(RensheProjectConfig, config_id)
            if config_id
            else await cls.latest(db, product.id)
        )
        if version is None or version.product_id != product.id:
            raise BusinessException("项目配置版本归属不一致")
        errors = configuration_errors(version.config)
        if getattr(plan, "renshe_config_id", None) != version.id:
            errors.extend(activation_errors(version.config))
        if errors:
            raise BusinessException("项目配置不可用：" + "；".join(errors))
        config = version.config
        exam = exam_type or (
            config["exam_types"][0] if len(config["exam_types"]) == 1 else None
        )
        application = application_type or (
            config["application_types"][0]
            if len(config["application_types"]) == 1
            else None
        )
        if (
            exam not in config["exam_types"]
            or application not in config["application_types"]
        ):
            raise BusinessException("请选择项目支持的考试类型和申报类型")
        (
            plan.product_type,
            plan.renshe_config_id,
            plan.renshe_config,
            plan.project_name,
        ) = code, version.id, dict(config), product.chinese_name
        for key in ("occupation_name", "occupation_code", "skill_level"):
            setattr(plan, key, config[key])
        plan.exam_type, plan.application_type = exam, application
