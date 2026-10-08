import ast
import asyncio
from datetime import datetime, timezone
import unittest
from pathlib import Path
import tempfile
import zipfile
from types import SimpleNamespace
from typing import get_args

from app.schemas.nisp import NispRegistrationStatus
from app.api.admin.nisp import get_export_signed_url
from app.integrations.nisp_storage import NispObjectStorage
from app.services.nisp_export import NispExportService
from app.port.exceptions import ValidationException


REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeExportStorage:
    async def download_file(self, key: str, path: Path) -> None:
        path.write_bytes(f"data:{key}".encode())


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _functions(source: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class NispOperationsTests(unittest.TestCase):
    def test_registration_response_accepts_every_database_refund_status(self):
        statuses = set(get_args(NispRegistrationStatus))

        self.assertIn("pending_refund_confirmation", statuses)
        self.assertIn("refund_processing", statuses)
        self.assertIn("refunded_closed", statuses)

    def test_public_batch_list_supports_level_isolation(self):
        source = _source("app/api/nisp.py")
        function = _functions(source)["list_batches"]
        body = ast.unparse(function)

        self.assertIn("level: Literal['1', '2'] | None=Query(", body)
        self.assertIn("if level is not None:", body)
        self.assertIn("stmt = stmt.where(NispExamBatch.level == level)", body)

    def test_refund_provider_payloads_are_parsed_with_wechat_pay_refund(self):
        source = _source("app/services/nisp_refund.py")

        self.assertNotIn("parse_refund_result", source)
        self.assertNotIn("result.out_trade_no != refund.out_trade_no", source)
        self.assertIn("expected_out_trade_no = order.out_trade_no if order else None", source)
        self.assertEqual(source.count("WechatPayRefund.from_payload(raw)"), 2)
        self.assertIn("async def handle_callback_raw", source)
        self.assertIn("parse_refund_notification", source)

    def test_nisp_refund_notification_is_dispatched_by_payment_callback(self):
        source = _source("app/api/payment.py")
        callback = _functions(source)["refund_callback"]
        body = ast.unparse(callback)

        self.assertIn("NispRefundService().handle_callback_raw", body)
        self.assertIn("RensheRefundService().handle_callback_raw", body)

    def test_nisp_export_worker_is_started_and_cancelled_with_application(self):
        source = _source("app/main.py")

        self.assertIn("from app.services.nisp_export import nisp_export_worker_loop", source)
        self.assertIn(
            "nisp_export_task = asyncio.create_task(nisp_export_worker_loop())",
            source,
        )
        self.assertIn("nisp_export_task.cancel()", source)
        self.assertIn("await nisp_export_task", source)

    def test_nisp_material_storage_enforces_material_specific_formats(self):
        async def test_storage(**kwargs):
            return await NispObjectStorage().save_source(**kwargs)

        async def fake_put(self, key, data, content_type):
            return None

        original_put = NispObjectStorage._put
        NispObjectStorage._put = fake_put
        try:
            valid_cases = [
                dict(material_type="id_card_both_sides", filename="id.pdf",
                     content_type="application/pdf"),
                dict(material_type="portrait_photo", filename="photo.jpg",
                     content_type="image/jpeg"),
                dict(material_type="xuexin_report", filename="report.pdf",
                     content_type=None),
                dict(material_type="application_form", filename="form.pdf",
                     content_type="application/octet-stream"),
            ]
            for kwargs in valid_cases:
                result = asyncio.run(test_storage(
                    user_id=2,
                    data=b"x",
                    **kwargs,
                ))
                self.assertEqual(len(result), 3)

            invalid_cases = [
                dict(material_type="portrait_photo", filename="photo.pdf",
                     content_type="application/pdf"),
                dict(material_type="xuexin_report", filename="report.docx",
                     content_type="application/octet-stream"),
                dict(material_type="application_form", filename="form.jpg",
                     content_type="image/jpeg"),
            ]
            for kwargs in invalid_cases:
                with self.assertRaises(ValidationException):
                    asyncio.run(test_storage(
                        user_id=2,
                        data=b"x",
                        **kwargs,
                    ))
        finally:
            NispObjectStorage._put = original_put

    def test_nisp_cancel_pending_payment_releases_reserved_coupon(self):
        source = _source("app/services/nisp_registration.py")
        cancel_body = ast.unparse(_functions(source)["cancel_pending_payment"])

        self.assertIn("release_order_coupon(", cancel_body)
        self.assertIn("coupon_code=order.coupon_code", cancel_body)
        self.assertIn("order.price = order.original_price or order.price", cancel_body)
        self.assertIn("order.original_price = None", cancel_body)
        self.assertIn("order.discount_amount = None", cancel_body)
        self.assertIn("order.coupon_code = None", cancel_body)

    def test_material_upload_receipts_are_validated_before_binding(self):
        source = _source("app/services/nisp_registration.py")

        self.assertIn('material.user_id != user_id', source)
        self.assertIn('material.material_type != material_type', source)
        self.assertIn('material.registration_id is not None', source)
        self.assertIn('replace_current=True', source)
        self.assertIn('current.is_current = False', source)

    def test_admin_registration_detail_exposes_review_materials(self):
        api = _source("app/api/admin/nisp.py")
        service = _source("app/services/nisp_registration.py")

        self.assertIn('"/registrations/{registration_id}"', api)
        self.assertIn("get_admin_registration", api)
        self.assertIn("NispMaterialResponse(", service)
        self.assertIn("signed_get_url(material.storage_key)", service)

    def test_full_export_builds_outer_zip_and_named_person_packages(self):
        service = NispExportService(storage=_FakeExportStorage())
        registrations = [
            SimpleNamespace(
                id=1,
                registration_no="NISP20001",
                candidate_idcard="110101200001011234",
                candidate_snapshot={"name": "张三"},
            ),
            SimpleNamespace(
                id=2,
                registration_no="NISP20002",
                candidate_idcard="110101200001015678",
                candidate_snapshot={"name": "张三"},
            ),
        ]
        materials = {}
        for reg in registrations:
            materials[reg.id] = {
                material_type: SimpleNamespace(
                    id=f"{reg.id}-{index}",
                    registration_id=reg.id,
                    material_type=material_type,
                    storage_key=f"nisp/materials/{reg.id}/{material_type}",
                    original_filename=(
                        f"{reg.candidate_snapshot['name']}-申请表.pdf"
                        if material_type == "application_form"
                        else None
                    ),
                )
                for index, material_type in enumerate(
                    (
                        "id_card_both_sides",
                        "portrait_photo",
                        "xuexin_report",
                        "application_form",
                    )
                )
            }
        manifests = service._manifests(registrations, materials)
        with tempfile.TemporaryDirectory() as raw:
            package_path = Path(raw) / "package.zip"
            asyncio.run(
                service._build_full_package(
                    job_id=1,
                    level="2",
                    excel_bytes=b"summary-excel",
                    registrations=registrations,
                    materials_by_registration=materials,
                    manifests=manifests,
                    destination=package_path,
                )
            )
            outer_path = Path(raw) / "package.zip"
            self.assertEqual(outer_path, package_path)
            with zipfile.ZipFile(outer_path) as outer:
                names = outer.namelist()
                self.assertIn("NISP二级报名汇总表.xlsx", names)
                self.assertIn("张三.zip", names)
                self.assertIn("张三-NISP20002.zip", names)
                with outer.open("张三.zip") as person_file:
                    person_path = Path(raw) / "person.zip"
                    person_path.write_bytes(person_file.read())
                    with zipfile.ZipFile(person_path) as person:
                        self.assertEqual(
                            set(person.namelist()),
                            {
                                "张三.pdf",
                                "张三-110101200001011234.jpg",
                                "张三-学籍报告.pdf",
                                "张三-申请表.pdf",
                            },
                        )

    def test_coupon_release_is_guarded_by_order_binding(self):
        source = _source("app/services/points_mall.py")

        self.assertIn("async def release_order_coupon", source)
        self.assertIn("redemption.order_id != order_id", source)
        self.assertIn('redemption.status = "unused"', source)
        self.assertIn("redemption.used_at = None", source)
        self.assertIn("redemption.order_id = None", source)


class NispSignedUrlRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_url_route_builds_expiry_without_undefined_helper(self):
        original = NispExportService.signed_url

        async def fake_signed_url(self, job_id: int) -> str:
            return "https://example.test/nisp-export.xlsx?signature=test"

        NispExportService.signed_url = fake_signed_url
        try:
            response = await get_export_signed_url(job_id=2, _admin=object())
        finally:
            NispExportService.signed_url = original

        self.assertEqual(response.code, 0)
        self.assertEqual(response.data.url, "https://example.test/nisp-export.xlsx?signature=test")
        self.assertGreater(response.data.expires_at, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
