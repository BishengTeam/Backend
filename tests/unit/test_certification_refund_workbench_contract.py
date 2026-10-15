from pathlib import Path


def test_admin_refund_lists_support_shared_workbench_filters():
    for module, batch_parameter in (
        ("h3c", "batch_id"),
        ("nisp", "batch_id"),
        ("renshe", "plan_id"),
    ):
        source = (Path(f"app/api/admin/{module}.py")).read_text(encoding="utf-8")
        service = (Path(f"app/services/{module}_refund.py")).read_text(encoding="utf-8")

        assert f"{batch_parameter}: int | None = Query(None, ge=1)" in source
        assert "keyword: str | None = Query(None, max_length=64)" in source
        assert 'pattern="^(pending|processing|abnormal)$"' in source
        assert f"{batch_parameter}: int | None = None" in service
        assert "registration_id: int | None" in service or "application_id: int | None" in service
        assert 'group == "pending"' in service
        assert 'group == "processing"' in service
        assert 'group == "abnormal"' in service


def test_refund_status_counts_are_materialized_before_group_filtering():
    for module in ("h3c", "nisp", "renshe"):
        source = (Path(f"app/services/{module}_refund.py")).read_text(encoding="utf-8")
        stats_position = source.index("stats_subquery =")
        group_position = source.index('if status:')
        assert stats_position < group_position


def test_refund_lists_expose_business_identity_and_request_time():
    expected_fields = (
        "out_trade_no",
        "candidate_name",
        "registration_no",
        "batch_id",
        "batch_name",
        "product_label",
    )
    for schema_name, time_field in (
        ("h3c_registration", "requested_at"),
        ("nisp", "requested_at"),
        ("renshe", "requested_at"),
    ):
        schema = (Path(f"app/schemas/{schema_name}.py")).read_text(encoding="utf-8")
        for field in expected_fields:
            assert field in schema
        assert time_field in schema
