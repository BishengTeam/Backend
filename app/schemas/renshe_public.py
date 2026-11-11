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
    result = plan.model_dump(include=PUBLIC_PLAN_FIELDS)
    config = {key: value for key, value in (plan.renshe_config or {}).items() if key in PUBLIC_CONFIG_FIELDS}
    if isinstance(config.get("rule_profile"), dict):
        config["rule_profile"] = {key: value for key, value in config["rule_profile"].items() if key in PUBLIC_PROFILE_FIELDS}
    result["renshe_config"] = config
    result["can_apply"] = config.get("qualification_rule") in {
        "related_major_graduated_degree_v1", "documented_employment_v1", "level_five_employment_v1", "level_three_v1",
    } and (plan.renshe_config or {}).get("reporting_policy", {}).get("contract_version") == 2
    return result
