"""Anonymous Renshe batch projection: only application-facing business data."""

from app.schemas.plan import PlanResponse

PUBLIC_PLAN_FIELDS = {
    "id", "product_type", "project_name", "name", "apply_start", "apply_end", "exam_date", "exam_location",
    "capacity", "occupation_name", "occupation_code", "skill_level", "exam_type", "application_type",
    "price_cents", "description", "enrolled", "status",
}
PUBLIC_CONFIG_FIELDS = {"qualification_rule", "education_levels", "institution_types", "materials", "rule_profile"}
PUBLIC_PROJECT_FIELDS = {
    "id", "product_id", "product_code", "product_name", "skill_level",
    "config_id", "price_cents", "sort_order", "is_active",
    "product_is_active", "product_renshe_status",
}
PUBLIC_PROFILE_FIELDS = {"source_conditions", "admission_paths", "occupation_name", "related_majors", "related_occupations", "project_limits", "sources"}
APPLICABLE_RULES = {
    "related_major_graduated_degree_v1",
    "documented_employment_v1",
    "level_five_employment_v1",
    "level_three_v1",
}


def _public_config(raw: dict | None) -> dict:
    config = {key: value for key, value in (raw or {}).items() if key in PUBLIC_CONFIG_FIELDS}
    if isinstance(config.get("rule_profile"), dict):
        config["rule_profile"] = {
            key: value
            for key, value in config["rule_profile"].items()
            if key in PUBLIC_PROFILE_FIELDS
        }
    return config


def _config_can_apply(raw: dict | None, config: dict) -> bool:
    return (
        config.get("qualification_rule") in APPLICABLE_RULES
        and (raw or {}).get("reporting_policy", {}).get("contract_version") == 2
    )

def public_plan(plan: PlanResponse) -> dict:
    result = plan.model_dump(include=PUBLIC_PLAN_FIELDS)
    config = _public_config(plan.renshe_config)
    result["renshe_config"] = config
    projects = []
    for item in plan.projects:
        project_config = _public_config(item.config)
        payload = item.model_dump(include=PUBLIC_PROJECT_FIELDS)
        payload["renshe_config"] = project_config
        payload["can_apply"] = bool(
            item.is_active
            and item.product_is_active
            and item.product_renshe_status == "available"
            and _config_can_apply(item.config, project_config)
        )
        projects.append(payload)
    result["projects"] = projects
    result["can_apply"] = _config_can_apply(
        plan.renshe_config, config
    ) or any(payload["can_apply"] for payload in projects)
    return result
