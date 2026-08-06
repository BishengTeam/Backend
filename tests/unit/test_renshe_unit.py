"""No-database contract tests for the first human-resources release."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.domain.renshe.src.index import (
    MAX_EXPORT_VOLUME_BYTES,
    RensheApplication,
    RensheApplicationVersion,
    RensheCleanupRun,
    RensheExportItem,
    RensheExportJob,
    RensheExportVolume,
    RensheMaterial,
    RensheRefundRequest,
    add_business_days,
    assert_application_transition,
    validate_material,
)
from app.main import app
from app.integrations.renshe_storage import RensheObjectStorage
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.admin import AdminOrderReview, AdminProfileUpdate
from app.schemas.renshe import RensheRefundCreate, RensheReviewCreate
from app.schemas.review import ReviewCreate
from app.schemas.user import RealnameSubmit, StudentSubmit
from app.services.renshe_export import (
    ExportCandidate,
    RensheExportService,
    build_registration_row,
    partition_export_candidates,
)
from app.services.renshe_cleanup import RensheCleanupService
from app.services.renshe_application import RensheApplicationService
from app.services.renshe_batch import RensheBatchService
from app.services.renshe_refund import RensheRefundService
from app.services.renshe_review import RensheReviewService
from app.services.review import ReviewService
from app.services.admin_order import AdminOrderService
from app.services.user import _assert_renshe_profile_unlocked
from app.domain.plan.src.index import Plan


ROOT = Path(__file__).resolve().parents[2]


def _route(method: str, path: str):
    return next(
        route
        for route in app.routes
        if path == getattr(route, "path", None)
        and method in (getattr(route, "methods", None) or set())
    )


def _db_context(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows


class _FakeDb:
    def __init__(self, *, scalars=(), rows=()):
        self._scalars = list(scalars)
        self._rows = list(rows)
        self.added = []
        self.executed = []
        self.commit_count = 0

    async def scalar(self, statement):
        self.executed.append(statement)
        return self._scalars.pop(0)

    async def execute(self, statement):
        self.executed.append(statement)
        value = self._rows.pop(0) if self._rows else []
        return _RowsResult(value)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, RensheRefundRequest) and value.id is None:
                value.id = 901

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, value):
        if value.id is None:
            value.id = 902
        if getattr(value, "created_at", None) is None:
            value.created_at = datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_application_state_machine_accepts_only_frozen_paths():
    allowed = (
        ("draft", "pending_payment"),
        ("pending_payment", "draft"),
        ("pending_payment", "pending_initial_review"),
        ("pending_initial_review", "initial_rejected"),
        ("pending_initial_review", "pending_external_review"),
        ("initial_rejected", "pending_initial_review"),
        ("pending_external_review", "external_rejected"),
        ("pending_external_review", "external_approved"),
        ("external_rejected", "pending_initial_review"),
        ("external_approved", "closed"),
    )
    for current, target in allowed:
        assert_application_transition(current, target)

    with pytest.raises(ValueError, match="状态不允许"):
        assert_application_transition("draft", "external_approved")
    with pytest.raises(ValueError, match="状态不允许"):
        assert_application_transition("closed", "draft")


@pytest.mark.parametrize(
    ("kind", "filename", "mime", "header", "expected"),
    [
        ("id_card_front", "front.JPG", "image/jpeg", b"\xff\xd8\xffx", ".jpg"),
        ("portrait", "photo.jpeg", "image/pjpeg; charset=binary", b"\xff\xd8\xffx", ".jpeg"),
        ("xuexin_registration", "record.PDF", "application/pdf", b"%PDF-1.7", ".pdf"),
    ],
)
def test_material_validation_accepts_matching_extension_mime_magic(
    kind, filename, mime, header, expected
):
    assert (
        validate_material(
            kind=kind,
            filename=filename,
            content_type=mime,
            size_bytes=len(header),
            header=header,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"filename": "front.png"}, "扩展名"),
        ({"content_type": "image/png"}, "MIME"),
        ({"header": b"not-a-jpeg"}, "文件头"),
        ({"size_bytes": 0}, "不能为空"),
        ({"size_bytes": 10 * 1024 * 1024 + 1}, "大小限制"),
    ],
)
def test_material_validation_rejects_mismatches(kwargs, message):
    values = {
        "kind": "student_card",
        "filename": "card.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 4,
        "header": b"\xff\xd8\xffx",
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        validate_material(**values)


def test_enterprise_identity_type_and_admin_certification_edits_are_rejected():
    payload = {
        "user_type": "enterprise",
        "last_name_zh": "张",
        "first_name_zh": "三",
        "real_name": "张三",
        "id_card_number": "11010519491231002X",
        "id_card_front_oss": "renshe/source/1/a.jpg",
        "id_card_back_oss": "renshe/source/1/b.jpg",
        "avatar_oss": "renshe/source/1/c.jpg",
        "political_status": "群众",
        "ethnicity": "汉族",
    }
    with pytest.raises(ValidationError):
        RealnameSubmit.model_validate(payload)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AdminProfileUpdate.model_validate({"identity_status": "verified"})


def test_student_certification_fields_are_all_required():
    complete = {
        "education": "bachelor",
        "school": "示例大学",
        "major": "信息安全",
        "enrollment_date": "2023-09-01",
        "student_card_oss": "renshe/source/1/student.jpg",
        "enrollment_pdf_oss": "renshe/source/1/xuexin.pdf",
        "degree_cert_oss": "renshe/source/1/education.jpg",
    }
    assert StudentSubmit.model_validate(complete).education == "bachelor"
    for field in complete:
        incomplete = complete.copy()
        incomplete.pop(field)
        with pytest.raises(ValidationError):
            StudentSubmit.model_validate(incomplete)


@pytest.mark.asyncio
async def test_profile_edits_lock_all_open_applications_before_state_check():
    draft = SimpleNamespace(id=1, status="draft", frozen_at=None)
    db = _FakeDb(rows=([draft],))

    await _assert_renshe_profile_unlocked(db, user_id=12)

    query_sql = str(db.executed[0])
    assert "renshe_application.status !=" in query_sql
    assert "ORDER BY renshe_application.id" in query_sql
    assert "FOR UPDATE" in query_sql

    pending = SimpleNamespace(id=2, status="pending_initial_review", frozen_at=None)
    locked_db = _FakeDb(rows=([draft, pending],))
    with pytest.raises(BusinessException, match="暂不能修改认证资料"):
        await _assert_renshe_profile_unlocked(locked_db, user_id=12)


@pytest.mark.asyncio
async def test_submission_locks_user_plan_orders_then_application():
    application = RensheApplication(id=41, user_id=7, plan_id=11, status="draft")
    plan = Plan(id=11, product_type="RS-ZY")
    closed_order = SimpleNamespace(id=60, status="closed")
    paid_order = SimpleNamespace(id=61, status="paid")
    db = _FakeDb(
        scalars=(11, SimpleNamespace(id=7), plan, application),
        rows=([closed_order, paid_order],),
    )

    locked_application, locked_plan, locked_paid_order = (
        await RensheApplicationService()._lock_submission_context(
            db, user_id=7, application_id=41
        )
    )

    assert locked_application is application
    assert locked_plan is plan
    assert locked_paid_order is paid_order
    statements = [str(statement) for statement in db.executed]
    assert "renshe_application.plan_id" in statements[0]
    assert "FOR UPDATE" not in statements[0]
    assert 'FROM "user"' in statements[1] and "FOR UPDATE" in statements[1]
    assert "FROM plan" in statements[2] and "FOR UPDATE" in statements[2]
    assert 'FROM "order"' in statements[3] and "FOR UPDATE" in statements[3]
    assert "ORDER BY \"order\".id" in statements[3]
    assert "FROM renshe_application" in statements[4]
    assert "FOR UPDATE" in statements[4]


@pytest.mark.asyncio
async def test_batch_latest_order_lock_is_deterministic_and_ignores_unbound_orders():
    first = SimpleNamespace(id=60, application_id=41, status="closed")
    latest = SimpleNamespace(id=61, application_id=41, status="paid")
    unbound = SimpleNamespace(id=62, application_id=None, status="paid")
    db = _FakeDb(rows=([first, latest, unbound],))

    result = await RensheBatchService._lock_latest_orders(db, plan_id=11)

    assert result == {41: latest}
    query_sql = str(db.executed[0])
    assert "ORDER BY \"order\".application_id, \"order\".id" in query_sql
    assert "FOR UPDATE" in query_sql


def test_rejection_requires_reason_and_changes():
    with pytest.raises(ValidationError):
        RensheReviewCreate(decision="rejected", reason="", required_changes=[])
    value = RensheReviewCreate(
        decision="rejected", reason=" 材料不清晰 ", required_changes=["student_card"]
    )
    assert value.reason == "材料不清晰"


def test_three_business_days_uses_asia_shanghai_calendar():
    # 2026-08-07 16:00 UTC is Saturday 00:00 in China. Three China-local
    # weekdays later is Wednesday 00:00, or Tuesday 16:00 UTC.
    start = datetime(2026, 8, 7, 16, tzinfo=timezone.utc)
    assert add_business_days(start, 3) == datetime(2026, 8, 11, 16, tzinfo=timezone.utc)
    assert add_business_days(datetime(2026, 8, 7, 9), 1) == datetime(2026, 8, 10, 9)
    with pytest.raises(ValueError):
        add_business_days(start, -1)


def test_user_and_admin_renshe_routes_are_registered_without_enterprise_routes():
    required = {
        ("POST", "/api/renshe/applications/draft"),
        ("POST", "/api/renshe/applications/{application_id}/submit"),
        ("POST", "/api/renshe/applications/{application_id}/refunds"),
        ("GET", "/api/renshe/verification-materials/{kind}/signed-url"),
        ("POST", "/admin/renshe/applications/{application_id}/initial-review"),
        ("POST", "/admin/renshe/applications/{application_id}/external-review"),
        ("POST", "/admin/renshe/refunds/{refund_id}/decision"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert required <= registered
    assert all("enterprise" not in path for _, path in registered)


def test_super_admin_guards_refund_decision_review_correction_and_batch_finalize():
    for method, path in (
        ("POST", "/admin/renshe/refunds/{refund_id}/decision"),
        ("POST", "/admin/renshe/reviews/{review_id}/corrections"),
        ("PUT", "/admin/certifications/{code}/plans/{plan_id}/finalize"),
    ):
        dependency_names = {
            getattr(dependency.call, "__name__", "")
            for dependency in _route(method, path).dependant.dependencies
        }
        assert "require_super_admin" in dependency_names


def test_v2_payment_paths_explicitly_block_renshe_orders():
    source = (ROOT / "app/services/payment.py").read_text(encoding="utf-8")
    assert "人社报名必须使用微信支付 API V3" in source
    assert "拒绝使用微信支付 V2 回调处理人社订单" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("review", "refund"))
async def test_legacy_admin_order_mutations_block_renshe_orders(
    monkeypatch, operation
):
    order = SimpleNamespace(id=61, application_id=41, status="paid")
    db = _FakeDb(rows=(order,))
    monkeypatch.setattr("app.services.admin_order.get_db_ctx", _db_context(db))
    service = AdminOrderService()

    with pytest.raises(BusinessException, match="专用报名审核和退款流程"):
        if operation == "review":
            await service.review_order(
                61, AdminOrderReview(action="approve", comment=None)
            )
        else:
            await service.refund_order(61)

    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_generic_review_endpoint_cannot_mutate_renshe_orders(monkeypatch):
    order = SimpleNamespace(id=61, application_id=41, status="paid")
    db = _FakeDb(rows=(order,))
    monkeypatch.setattr("app.services.review.get_db_ctx", _db_context(db))

    with pytest.raises(ConflictException, match="专用报名审核和退款流程"):
        await ReviewService().create_review(
            reviewer_id=5,
            data=ReviewCreate(
                target_type="order",
                target_id=61,
                action="approve",
                comment=None,
            ),
        )

    assert db.commit_count == 0
    assert not db.added


def test_refund_freeze_and_batch_finalization_guards_are_present():
    refund_source = (ROOT / "app/services/renshe_refund.py").read_text(encoding="utf-8")
    batch_source = (ROOT / "app/services/renshe_batch.py").read_text(encoding="utf-8")
    assert "application.frozen_at = now" in refund_source
    assert "application.freeze_reason" in refund_source
    assert "PENDING_FINALIZATION_STATUSES" in batch_source
    assert "batch_finalize" in batch_source
    assert "RensheCleanupRun" in batch_source


@pytest.mark.asyncio
async def test_requesting_refund_immediately_pauses_scheduled_cleanup(monkeypatch):
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    application = SimpleNamespace(
        id=41,
        user_id=7,
        plan_id=11,
        current_version_id=51,
        status="pending_external_review",
        frozen_at=None,
        freeze_reason=None,
    )
    order = SimpleNamespace(id=61, price=50000, status="paid")
    cleanup_run = SimpleNamespace(id=71, status="scheduled")
    db = _FakeDb(scalars=(order, application, cleanup_run, None, None))
    monkeypatch.setattr("app.services.renshe_refund.get_db_ctx", _db_context(db))
    monkeypatch.setattr(RensheRefundService, "_now", staticmethod(lambda: now))

    response = await RensheRefundService().request_refund(
        user_id=7,
        application_id=41,
        data=RensheRefundCreate(request_kind="normal", reason_code="personal"),
    )

    assert response.status == "requested"
    assert application.frozen_at == now
    assert application.freeze_reason == "refund:901"
    assert db.commit_count == 1
    cleanup_lock_sql = str(db.executed[2])
    assert "renshe_cleanup_run.status IN" in cleanup_lock_sql
    assert "FOR UPDATE" in cleanup_lock_sql
    update_sql = next(
        str(statement.compile(compile_kwargs={"literal_binds": True}))
        for statement in db.executed
        if statement.__class__.__name__ == "Update"
    )
    assert "UPDATE renshe_cleanup_run" in update_sql
    assert "status='paused'" in update_sql.replace(" ", "")
    assert "paused_reason='active_refunds'" in update_sql.replace(" ", "")
    assert "status = 'scheduled'" in update_sql


@pytest.mark.asyncio
async def test_refund_is_rejected_while_batch_cleanup_is_running(monkeypatch):
    application = SimpleNamespace(
        id=41,
        user_id=7,
        plan_id=11,
        current_version_id=51,
        status="pending_external_review",
        frozen_at=None,
        freeze_reason=None,
    )
    order = SimpleNamespace(id=61, price=50000, status="paid")
    db = _FakeDb(
        scalars=(
            order,
            application,
            SimpleNamespace(id=88, status="running"),
        )
    )
    monkeypatch.setattr("app.services.renshe_refund.get_db_ctx", _db_context(db))

    with pytest.raises(ConflictException, match="正在清理"):
        await RensheRefundService().request_refund(
            user_id=7,
            application_id=41,
            data=RensheRefundCreate(request_kind="normal", reason_code="personal"),
        )

    assert db.commit_count == 0
    assert not db.added


def test_renshe_models_and_migration_contain_version_export_refund_cleanup_contracts():
    table_names = {
        model.__tablename__
        for model in (
            RensheApplication,
            RensheApplicationVersion,
            RensheMaterial,
            RensheRefundRequest,
            RensheExportJob,
            RensheExportVolume,
            RensheExportItem,
            RensheCleanupRun,
        )
    }
    migration = (
        ROOT / "alembic/versions/rsh001_add_renshe_registration_domain.py"
    ).read_text(encoding="utf-8")
    for table_name in table_names:
        assert f'"{table_name}"' in migration
    assert MAX_EXPORT_VOLUME_BYTES == 10 * 1024 * 1024 * 1024
    assert "10737418240" in migration
    assert "application_id" in migration


def test_account_cleanup_preserves_users_referenced_by_renshe_history():
    source = (ROOT / "app/services/cleanup.py").read_text(encoding="utf-8")
    assert "~exists().where(RensheApplication.user_id == User.id)" in source
    assert "ORDER_TIMEOUT_INTERVAL_SECONDS = 60" in source


def test_export_partition_never_splits_a_candidate_and_respects_estimates():
    partitions = partition_export_candidates(
        [(1, 5_000), (2, 5_000), (3, 5_000)],
        max_volume_bytes=30_000,
        fixed_overhead_bytes=1_000,
    )
    assert partitions == [[1, 2], [3]]
    with pytest.raises(ValueError, match="无法放入单个分卷"):
        partition_export_candidates(
            [(1, 30_000)],
            max_volume_bytes=30_000,
            fixed_overhead_bytes=1_000,
        )


def test_export_row_uses_frozen_student_mapping_and_leaves_work_fields_blank():
    candidate = ExportCandidate(
        application_id=1,
        version_id=2,
        estimated_size_bytes=100,
        realname_snapshot={
            "real_name": "张三",
            "gender": "男",
            "birth_date": "2003-01-02",
            "id_card_number": "11010519491231002X",
            "ethnicity": "汉族",
            "political_status": "群众",
        },
        student_snapshot={
            "education": "bachelor",
            "enrollment_date": "2023-09-01",
        },
        form_data={
            "contact_phone": "13800138000",
            "mailing_address": "成都市",
            "email": "student@example.com",
        },
        materials=(),
    )
    row = build_registration_row(candidate, Plan(product_type="RS-ZY", name="批次"))
    assert len(row) == 25
    assert row[5] == "院校学生"
    assert row[6] == "大学本科"
    assert row[12] == "应届毕业生"
    assert row[14] == "四级"
    assert row[11] is None
    assert row[18] is None
    assert row[21:23] == ["学历型", "新考"]


def test_export_and_private_download_routes_are_registered():
    required = {
        ("POST", "/admin/renshe/plans/{plan_id}/exports"),
        ("GET", "/admin/renshe/plans/{plan_id}/exports"),
        ("GET", "/admin/renshe/exports/{job_id}"),
        ("POST", "/admin/renshe/exports/{job_id}/retry"),
        ("GET", "/admin/renshe/materials/{material_id}/signed-url"),
        (
            "GET",
            "/admin/renshe/users/{user_id}/verification-materials/{kind}/signed-url",
        ),
        ("GET", "/admin/renshe/export-volumes/{volume_id}/signed-url"),
        ("GET", "/admin/renshe/plans/{plan_id}/cleanup-runs"),
        ("POST", "/admin/renshe/cleanup-runs/{run_id}/retry"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert required <= registered


@pytest.mark.asyncio
async def test_admin_application_list_masks_pii_and_applies_new_filters(monkeypatch):
    now = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    application = SimpleNamespace(
        id=9,
        plan_id=3,
        user_id=4,
        current_version_id=10,
        status="pending_external_review",
        submitted_at=now,
        frozen_at=None,
        created_at=now,
        updated_at=now,
        draft_data=None,
    )
    version = SimpleNamespace(
        realname_snapshot={
            "real_name": "张三",
            "id_card_number": "11010519491231002X",
        },
        form_data={"contact_phone": "13800138000"},
    )
    order = SimpleNamespace(id=77, status="paid")
    db = _FakeDb(scalars=(1,), rows=([(application, version, order, None)],))
    monkeypatch.setattr("app.services.renshe_review.get_db_ctx", _db_context(db))

    result = await RensheReviewService().list_applications(
        plan_id=3,
        status="pending_external_review",
        payment_status="paid",
        keyword="张三",
        submitted_at_start=now,
        submitted_at_end=now,
        page=1,
        page_size=20,
    )

    item = result.items[0]
    assert item.id_card_masked == "1101**********002X"
    assert item.contact_phone_masked == "138****8000"
    assert "11010519491231002X" not in item.model_dump_json()
    assert "13800138000" not in item.model_dump_json()
    query_sql = str(db.executed[-1])
    assert "renshe_application.submitted_at" in query_sql
    assert "renshe_application.draft_data" in query_sql
    assert '"order".status' in query_sql
    assert "lower" in query_sql.lower()


def test_admin_application_openapi_exposes_filter_and_masked_response_contract():
    app.openapi_schema = None
    schema = app.openapi()
    operation = schema["paths"]["/admin/renshe/applications"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}
    assert {
        "plan_id",
        "status",
        "payment_status",
        "keyword",
        "submitted_at_start",
        "submitted_at_end",
        "page",
        "page_size",
    } <= parameter_names
    list_item = schema["components"]["schemas"]["RensheAdminApplicationListItem"]
    assert {
        "id_card_masked",
        "contact_phone_masked",
        "payment_status",
        "submitted_at",
    } <= set(list_item["properties"])


def test_export_worker_has_progress_heartbeat_integrity_and_access_audit():
    source = (ROOT / "app/services/renshe_export.py").read_text(encoding="utf-8")
    assert "candidate_processed" in source
    assert "_heartbeat_loop" in source
    assert source.count("asyncio.create_task(self._heartbeat_loop(job_id))") == 1
    assert source.index("asyncio.create_task(self._heartbeat_loop(job_id))") < source.index(
        "await self._run_claimed_job(job_id)"
    )
    assert "导出材料完整性校验失败" in source
    assert 'action="export.download"' in source
    assert 'action = "material.download" if download else "material.preview"' in source


@pytest.mark.asyncio
async def test_generated_archives_use_oss_multipart_resumable_upload(
    monkeypatch, tmp_path
):
    archive = tmp_path / "volume.zip"
    archive.write_bytes(b"zip-data")
    bucket = object()
    upload_result = SimpleNamespace(status=200)
    resumable_upload = MagicMock(return_value=upload_result)
    fake_oss2 = SimpleNamespace(resumable_upload=resumable_upload)
    storage = RensheObjectStorage()
    storage.storage_type = "aliyun_oss"
    monkeypatch.setitem(sys.modules, "oss2", fake_oss2)
    monkeypatch.setattr(storage, "_oss_bucket", lambda: bucket)

    await storage.upload_file("renshe/exports/volume.zip", archive, "application/zip")

    resumable_upload.assert_called_once_with(
        bucket,
        "renshe/exports/volume.zip",
        str(archive),
        multipart_threshold=100 * 1024 * 1024,
        part_size=16 * 1024 * 1024,
        num_threads=4,
        headers={"Content-Type": "application/zip"},
    )


@pytest.mark.asyncio
async def test_export_window_closes_when_batch_cleanup_is_due():
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    plan = SimpleNamespace(id=11, cleanup_due_at=now)
    service = RensheExportService()
    service._now = lambda: now

    with pytest.raises(ConflictException, match="已到清理期限"):
        await service._assert_export_window_open(_FakeDb(), plan)


@pytest.mark.asyncio
async def test_export_retry_locks_plan_before_job(monkeypatch):
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    plan = SimpleNamespace(
        id=11,
        product_type="RS-ZY",
        cleanup_due_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    job = SimpleNamespace(
        id=71,
        plan_id=11,
        status="failed",
        candidate_processed=3,
        volume_count=1,
        heartbeat_at=now,
        started_at=now,
        finished_at=now,
        retry_count=0,
        last_error="failed",
    )
    db = _FakeDb(scalars=(11, plan, None, job))
    monkeypatch.setattr("app.services.renshe_export.get_db_ctx", _db_context(db))
    service = RensheExportService()
    service.get_job = AsyncMock(return_value="retried")

    result = await service.retry_job(job_id=71, admin_id=5)

    assert result == "retried"
    assert job.status == "queued"
    assert job.retry_count == 1
    statements = [str(statement) for statement in db.executed]
    assert "renshe_export_job.plan_id" in statements[0]
    assert "FOR UPDATE" not in statements[0]
    assert "FROM plan" in statements[1] and "FOR UPDATE" in statements[1]
    assert "renshe_cleanup_run.status" in statements[2]
    assert "FROM renshe_export_job" in statements[3]
    assert "FOR UPDATE" in statements[3]


@pytest.mark.asyncio
async def test_user_verification_material_url_is_scoped_and_audited(monkeypatch):
    realname = SimpleNamespace(
        id_card_front_oss="renshe/source/12/front.jpg",
        id_card_back_oss="renshe/source/12/back.jpg",
        avatar_oss="renshe/source/12/portrait.jpg",
    )
    student = SimpleNamespace(
        student_card_oss="renshe/source/12/student.jpg",
        enrollment_pdf_oss="renshe/source/12/xuexin.pdf",
        degree_cert_oss="renshe/source/12/education.jpg",
    )
    db = _FakeDb(scalars=(realname, student))
    storage = SimpleNamespace(
        signed_get_url=AsyncMock(return_value="https://private.example/signed")
    )
    service = RensheExportService(storage=storage)
    audit = AsyncMock()
    monkeypatch.setattr("app.services.renshe_export.get_db_ctx", _db_context(db))
    monkeypatch.setattr(service, "_access_audit", audit)

    response = await service.user_verification_material_signed_url(
        user_id=12,
        kind="student_card",
        download=False,
        ip_address="127.0.0.1",
    )

    assert response.url == "https://private.example/signed"
    assert response.expires_in <= 300
    storage.signed_get_url.assert_awaited_once_with(
        "renshe/source/12/student.jpg", download_filename=None
    )
    assert audit.await_args.kwargs["actor_type"] == "user"
    assert audit.await_args.kwargs["actor_id"] == 12
    assert audit.await_args.kwargs["object_id"] == 12
    dependency_names = {
        getattr(dependency.call, "__name__", "")
        for dependency in _route(
            "GET", "/api/renshe/verification-materials/{kind}/signed-url"
        ).dependant.dependencies
    }
    assert "get_current_user" in dependency_names


@pytest.mark.asyncio
async def test_profile_review_record_and_status_change_share_one_transaction(monkeypatch):
    target = SimpleNamespace(
        status="pending",
        snapshot={"enrollment_date": "2023-09-01", "school": "原院校"},
        enrollment_date=date(2024, 9, 1),
        school="新院校",
        verified_at="2026-08-01T00:00:00+00:00",
    )
    db = _FakeDb(rows=(target,))
    monkeypatch.setattr("app.services.review.get_db_ctx", _db_context(db))

    response = await ReviewService().create_review(
        reviewer_id=5,
        data=ReviewCreate(
            target_type="student",
            target_id=12,
            action="reject",
            comment="材料不清晰",
        ),
    )

    assert response.id == 902
    assert target.status == "rejected"
    assert target.enrollment_date == date(2023, 9, 1)
    assert target.school == "原院校"
    assert target.snapshot is None
    assert target.verified_at is None
    assert db.commit_count == 1
    assert len(db.added) == 1
    assert db.added[0].target_type == "student"
    assert db.added[0].action == "reject"


@pytest.mark.asyncio
async def test_paused_cleanup_query_skips_batches_with_active_refunds(monkeypatch):
    db = _FakeDb(scalars=(None,))
    monkeypatch.setattr("app.services.renshe_cleanup.get_db_ctx", _db_context(db))

    resumed = await RensheCleanupService(storage=SimpleNamespace())._resume_one_paused_run()

    assert resumed is False
    query_sql = str(db.executed[0])
    assert "renshe_cleanup_run.status" in query_sql
    assert "NOT (EXISTS" in query_sql
    assert "renshe_application.plan_id = renshe_cleanup_run.plan_id" in query_sql


@pytest.mark.asyncio
async def test_cleanup_finalization_locks_orders_applications_then_cleanup_run():
    run = SimpleNamespace(id=71, status="running")
    db = _FakeDb(scalars=(run,))

    locked_run = await RensheCleanupService._lock_cleanup_mutation_rows(
        db, application_ids=[41, 42], run_id=71
    )

    assert locked_run is run
    statements = [str(statement) for statement in db.executed]
    assert 'FROM "order"' in statements[0]
    assert "ORDER BY \"order\".application_id, \"order\".id" in statements[0]
    assert "FOR UPDATE" in statements[0]
    assert "FROM renshe_application" in statements[1]
    assert "ORDER BY renshe_application.id" in statements[1]
    assert "FOR UPDATE" in statements[1]
    assert "FROM renshe_cleanup_run" in statements[2]
    assert "FOR UPDATE" in statements[2]


@pytest.mark.asyncio
async def test_cleanup_claim_locks_plan_and_waits_for_active_export(monkeypatch):
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    run = SimpleNamespace(id=71, plan_id=11, status="scheduled")
    db = _FakeDb(
        scalars=(SimpleNamespace(id=11), run, 0, 1),
        rows=([(71, 11)],),
    )
    monkeypatch.setattr("app.services.renshe_cleanup.get_db_ctx", _db_context(db))
    monkeypatch.setattr(RensheCleanupService, "_now", staticmethod(lambda: now))

    claimed = await RensheCleanupService(storage=SimpleNamespace())._claim_due_run()

    assert claimed is None
    assert run.status == "scheduled"
    assert db.commit_count == 0
    statements = [str(statement) for statement in db.executed]
    assert "FROM plan" in statements[1] and "FOR UPDATE" in statements[1]
    assert "FROM renshe_cleanup_run" in statements[2]
    assert "FOR UPDATE" in statements[2]
    assert "FROM renshe_export_job" in statements[4]


def test_cleanup_worker_removes_objects_and_sensitive_snapshots_but_keeps_audit():
    source = (ROOT / "app/services/renshe_cleanup.py").read_text(encoding="utf-8")
    assert "await self.storage.delete_many(storage_keys)" in source
    assert "realname_snapshot={}" in source
    assert "student_snapshot={}" in source
    assert "candidate_idcard=\"***\"" in source
    assert 'action="cleanup.execute"' in source
    assert "RensheAuditLog" in source


def test_cleanup_retry_requires_super_admin():
    dependency_names = {
        getattr(dependency.call, "__name__", "")
        for dependency in _route(
            "POST", "/admin/renshe/cleanup-runs/{run_id}/retry"
        ).dependant.dependencies
    }
    assert "require_super_admin" in dependency_names
