"""看板响应契约：DashboardResponse 必须放行 service 返回的全部字段。

历史上 schema 只声明核心 7 字段，quiz/趋势等已计算字段被 Pydantic 静默丢弃，
前端拿到 undefined。此测试防止响应字段再被过滤。
"""

from app.schemas.admin import DashboardResponse


def _full_dashboard_payload() -> dict:
    return dict(
        total_users=1,
        total_orders=2,
        recent_orders_30d=1,
        paid_orders=1,
        revenue_fen=100,
        recent_revenue_30d_fen=100,
        conversion_rate=50.0,
        payment_success_rate=80.5,
        ai_conversations_total=9,
        ai_conversations_30d=3,
        tickets_total=4,
        tickets_waiting_manual=1,
        manual_transfer_rate=25.0,
        quiz_questions_total=10,
        quiz_libraries_total=2,
        quiz_attempts_today=5,
        quiz_attempts_30d=50,
        h3c_pending_review=1,
        h3c_approved=2,
        nisp_pending_review=0,
        nisp_approved=1,
        courses_total=3,
        competitions_active=1,
        competition_registrations=7,
        classrooms_active=0,
        revenue_trend=[{"date": "2026-10-01", "value": 100}],
        user_trend=[{"date": "2026-10-01", "value": 1}],
        quiz_trend=[{"date": "2026-10-01", "value": 5}],
        order_type_distribution=[{"name": "cert", "value": 2}],
        cert_status={"h3c": {"pending_review": 1, "approved": 2}},
    )


def test_response_keeps_all_service_fields() -> None:
    payload = _full_dashboard_payload()
    dumped = DashboardResponse(**payload).model_dump()
    for key in payload:
        assert key in dumped, f"看板字段被响应模型过滤: {key}"


def test_three_new_metrics_values() -> None:
    dumped = DashboardResponse(**_full_dashboard_payload()).model_dump()
    assert dumped["ai_conversations_total"] == 9
    assert dumped["manual_transfer_rate"] == 25.0
    assert dumped["payment_success_rate"] == 80.5


def test_response_tolerates_partial_payload() -> None:
    """旧调用方只传核心字段时，新增字段给默认值而不是报错。"""
    dumped = DashboardResponse(
        total_users=1,
        total_orders=0,
        recent_orders_30d=0,
        paid_orders=0,
        revenue_fen=0,
        recent_revenue_30d_fen=0,
        conversion_rate=0,
    ).model_dump()
    assert dumped["payment_success_rate"] == 0
    assert dumped["revenue_trend"] == []
