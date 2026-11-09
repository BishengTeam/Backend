"""Anonymous Renshe batch projection: only application-facing business data."""

from app.schemas.plan import PlanResponse

PUBLIC_PLAN_FIELDS = {
    "id", "product_type", "project_name", "name", "apply_start", "apply_end", "exam_date", "exam_location",
    "capacity", "occupation_name", "occupation_code", "skill_level", "exam_type", "application_type",
    "price_cents", "description", "enrolled", "status",
}
PUBLIC_CONFIG_FIELDS = {"qualification_rule", "education_levels", "institution_types", "materials", "rule_profile"}
PUBLIC_PROFILE_FIELDS = {"source_conditions", "admission_paths", "occupation_name", "related_majors", "related_occupations", "project_limits", "sources"}

def public_plan(plan: PlanResponse) -> dict:
    # v2: renshe_config 在 products[0] 上
    _pp_config = {}
    if getattr(plan, "products", None):
        _pp_config = getattr(plan.products[0], "renshe_config", None) or {}
    else:
        _pp_config = getattr(plan, "renshe_config", None) or {}
    # 兼容：合并 plan 级和 product 级字段
    # 兼容 Pydantic model 和 SQLAlchemy model
    if hasattr(plan, "model_dump"):
        result = {key: value for key, value in plan.model_dump(exclude={"products", "time_segments"}).items() if key in PUBLIC_PLAN_FIELDS}
    else:
        result = {key: getattr(plan, key, None) for key in PUBLIC_PLAN_FIELDS}
    config = {key: value for key, value in _pp_config.items() if key in PUBLIC_CONFIG_FIELDS}
    if isinstance(config.get("rule_profile"), dict):
        config["rule_profile"] = {key: value for key, value in config["rule_profile"].items() if key in PUBLIC_PROFILE_FIELDS}
    result["renshe_config"] = config
    result["can_apply"] = config.get("qualification_rule") in {
        "related_major_graduated_degree_v1", "documented_employment_v1", "level_five_employment_v1", "level_three_v1",
    } and _pp_config.get("reporting_policy", {}).get("contract_version") == 2
    return result
